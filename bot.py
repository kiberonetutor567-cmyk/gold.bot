import asyncio
import time
import logging
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, TelegramObject, WebAppInfo
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from typing import Callable, Dict, Any, Awaitable

from config import BOT_TOKEN, ADMIN_ID, COMMISSION_RATE
from database import (
    init_db, create_order, complete_order, get_last_orders,
    add_referral, confirm_referral_if_first_purchase,
    get_referral_stats, mark_tier_claimed, add_referral_reward
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─── Защита от флуда ──────────────────────────────────────────────────────────

THROTTLE_SECONDS = 1.5  # минимальный интервал между сообщениями от одного юзера


class ThrottlingMiddleware(BaseMiddleware):
    """
    Игнорирует сообщения/нажатия от пользователя, если они идут чаще
    чем раз в THROTTLE_SECONDS. Без ответа спамеру — просто молча отбрасывает.
    """
    def __init__(self, rate_limit: float = THROTTLE_SECONDS):
        self.rate_limit = rate_limit
        self.last_event: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and user.id != ADMIN_ID:
            now = time.monotonic()
            last = self.last_event.get(user.id, 0)
            if now - last < self.rate_limit:
                # Слишком часто — молча игнорируем это событие
                return
            self.last_event[user.id] = now

        return await handler(event, data)


dp.message.middleware(ThrottlingMiddleware())
dp.callback_query.middleware(ThrottlingMiddleware())

# ─── Твои реквизиты и инфо ───────────────────────────────────────────────────
SBP_PHONE = "+7XXXXXXXXXX"      # ← ВСТАВЬ СВОЙ НОМЕР
SBP_BANK = "Сбер/Тинькофф"     # ← ВСТАВЬ СВОЙ БАНК
OWNER_CONTACT = "@твой_юзернейм"  # ← ВСТАВЬ СВОЙ ЮЗЕРНЕЙМ
SELL_RATE = 0.85  # курс при продаже голды (клиент получает 85% от номинала, например)

# ← ВСТАВЬ СЮДА СВОЮ ССЫЛКУ С NETLIFY ПОСЛЕ ЗАГРУЗКИ miniapp.html
MINI_APP_URL = "deft-longma-0469be.netlify.app"

# Реферальные пороги: количество подтверждённых друзей -> награда голдой
REFERRAL_TIERS = [
    (20, 100),
    (50, 200),
    (100, 300),
]


# ─── Состояния FSM ───────────────────────────────────────────────────────────

class BuyState(StatesGroup):
    waiting_gold_amount = State()
    waiting_payment = State()
    waiting_admin_confirm = State()
    waiting_screenshot = State()

class SellState(StatesGroup):
    waiting_gold_amount = State()
    waiting_card = State()
    waiting_screenshot = State()

class CalcState(StatesGroup):
    waiting_amount = State()

class VipState(StatesGroup):
    waiting_payment = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def main_menu():
    """Главное меню — reply-кнопки 2 в ряд, как у конкурента"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Купить голду"), KeyboardButton(text="⬆️ Продать голду")],
            [KeyboardButton(text="📈 Вывести голду"), KeyboardButton(text="🍷 Рассчитать")],
            [KeyboardButton(text="🔗 Профиль"), KeyboardButton(text="💼 Игры")],
            [KeyboardButton(text="🤝 Рефералы"), KeyboardButton(text="📊 Отзывы")],
            [KeyboardButton(text="👑 VIP"), KeyboardButton(text="🎫 Промокод")],
            [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="🍷 О нас")],
        ],
        resize_keyboard=True
    )

def paid_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Оплатил")],
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )

def admin_confirm_kb(order_id: int, user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Оплата получена", callback_data=f"confirm_{order_id}_{user_id}"),
            InlineKeyboardButton(text="❌ Не получена", callback_data=f"reject_{order_id}_{user_id}")
        ]
    ])

def admin_sell_confirm_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Скин куплен, перевожу деньги", callback_data=f"sellconfirm_{user_id}")]
    ])

def vip_tiers_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👑 Base — 300₽", callback_data="vip_base"),
            InlineKeyboardButton(text="👑 Pro — 800₽", callback_data="vip_pro"),
            InlineKeyboardButton(text="👑 Premium — 1500₽", callback_data="vip_premium"),
        ]
    ])

def vip_pay_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Оплатил")],
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )

def admin_vip_confirm_kb(user_id: int, tier_key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить VIP", callback_data=f"vipconfirm_{user_id}_{tier_key}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"vipreject_{user_id}")
        ]
    ])

def webapp_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Открыть терминал", web_app=WebAppInfo(url=MINI_APP_URL))]
    ])


# ─── Тексты разделов ─────────────────────────────────────────────────────────

ABOUT_TEXT = (
    "🍷 <b>О нас</b>\n\n"
    "👋 Привет! Это бот для покупки и продажи внутриигровой голды в Standoff 2.\n"
    "💱 Быстрый обмен, честные расчёты\n"
    "⚡ Работаю каждый день\n"
    f"✉️ Связь с владельцем: {OWNER_CONTACT}"
)

GAMES_TEXT = (
    "💼 <b>Поддерживаемые игры</b>\n\n"
    "🎯 Standoff 2 — покупка и продажа голды\n\n"
    "Скоро добавим больше игр!"
)

STUB_TEXTS = {
    "📈 Вывести голду": "📈 <b>Вывод голды</b>\n\nЭтот раздел скоро будет доступен!",
    "📊 Отзывы": "📊 <b>Отзывы</b>\n\nРаздел с отзывами клиентов скоро будет доступен!",
    "🎫 Промокод": "🎫 <b>Промокоды</b>\n\nВвод промокодов скоро будет доступен!",
    "🎁 Бонус": "🎁 <b>Бонусы</b>\n\nСистема бонусов скоро будет доступна!",
}

# VIP-тарифы: название -> (срок_дней, цена_рублей, описание)
VIP_TIERS = {
    "base": {"label": "👑 Base", "days": 7, "price": 300},
    "pro": {"label": "👑 Pro", "days": 7, "price": 800},
    "premium": {"label": "👑 Premium", "days": 7, "price": 1500},
}

VIP_INTRO_TEXT = (
    "👑 <b>VIP-подписка</b>\n\n"
    "VIP даёт:\n"
    "• Льготный курс покупки 1 раз в день (VIP-день)\n"
    "• Значок VIP в профиле\n"
    "• Приоритет в обработке заявок\n\n"
    "<b>Тарифы (7 дней):</b>\n"
    f"👑 Base — {VIP_TIERS['base']['price']} ₽\n"
    f"👑 Pro — {VIP_TIERS['pro']['price']} ₽\n"
    f"👑 Premium — {VIP_TIERS['premium']['price']} ₽\n\n"
    "Выберите тариф:"
)


# ─── /start ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # Проверяем реферальную ссылку: /start ref_123456789
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].replace("ref_", ""))
            added = await add_referral(referrer_id, message.from_user.id)
            if added:
                try:
                    await bot.send_message(
                        chat_id=referrer_id,
                        text="🤝 По твоей ссылке пришёл новый человек!\n"
                             "Награда засчитается после его первой покупки.",
                    )
                except Exception:
                    pass
        except ValueError:
            pass

    await message.answer(
        "🏠 Вы в главном меню\nВыберите раздел:",
        reply_markup=main_menu()
    )
    await message.answer(
        "🎮 Или открой визуальный терминал обмена:",
        reply_markup=webapp_kb()
    )


@dp.message(F.text == "❌ Отменить")
async def cancel_any(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=main_menu())


# ─── Заглушки ────────────────────────────────────────────────────────────────

@dp.message(F.text.in_(STUB_TEXTS.keys()))
async def stub_sections(message: Message):
    await message.answer(STUB_TEXTS[message.text], parse_mode="HTML")


@dp.message(F.text == "💼 Игры")
async def games_section(message: Message):
    await message.answer(GAMES_TEXT, parse_mode="HTML")


@dp.message(F.text == "🍷 О нас")
async def about_section(message: Message):
    await message.answer(ABOUT_TEXT, parse_mode="HTML")


# ─── VIP ─────────────────────────────────────────────────────────────────────

@dp.message(F.text == "👑 VIP")
async def vip_section(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(VIP_INTRO_TEXT, parse_mode="HTML", reply_markup=vip_tiers_kb())


@dp.callback_query(F.data.startswith("vip_"))
async def vip_tier_selected(callback: CallbackQuery, state: FSMContext):
    tier_key = callback.data.split("_")[1]
    tier = VIP_TIERS.get(tier_key)
    if not tier:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.update_data(vip_tier=tier_key)
    await state.set_state(VipState.waiting_payment)

    await callback.message.edit_text(
        f"{tier['label']} — {tier['days']} дней\n\n"
        f"💰 Стоимость: <b>{tier['price']} ₽</b>\n\n"
        f"Переведи по СБП:\n"
        f"📱 <b>{SBP_PHONE}</b>\n"
        f"🏦 {SBP_BANK}\n\n"
        f"После оплаты нажми <b>«✅ Оплатил»</b> в чате 👇",
        parse_mode="HTML"
    )
    await callback.message.answer(
        "Жду подтверждение оплаты:",
        reply_markup=vip_pay_kb()
    )
    await callback.answer()


@dp.message(VipState.waiting_payment, F.text == "✅ Оплатил")
async def vip_process_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    tier_key = data.get("vip_tier")
    tier = VIP_TIERS.get(tier_key)
    if not tier:
        await state.clear()
        await message.answer("⚠️ Ошибка, попробуй заново через меню VIP.", reply_markup=main_menu())
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    await state.clear()
    await message.answer(
        "⏳ Проверяю поступление оплаты...\nОжидай подтверждения (1-5 минут).",
        reply_markup=main_menu()
    )

    caption = (
        f"👑 <b>Запрос VIP — {tier['label']}</b>\n\n"
        f"👤 Клиент: {username} (id: <code>{user.id}</code>)\n"
        f"📅 Срок: {tier['days']} дней\n"
        f"💵 Сумма: <b>{tier['price']} ₽</b>\n\n"
        f"Проверь СБП и подтверди 👇"
    )
    try:
        await bot.send_message(
            chat_id=ADMIN_ID, text=caption, parse_mode="HTML",
            reply_markup=admin_vip_confirm_kb(user.id, tier_key)
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа о VIP: {e}")


@dp.message(VipState.waiting_payment)
async def vip_payment_wrong(message: Message):
    await message.answer("⏳ Нажми <b>«✅ Оплатил»</b> после перевода.", parse_mode="HTML")


@dp.callback_query(F.data.startswith("vipconfirm_"))
async def admin_vip_confirm(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    parts = callback.data.split("_")
    user_id, tier_key = int(parts[1]), parts[2]
    tier = VIP_TIERS.get(tier_key)

    await callback.message.edit_text(callback.message.text + "\n\n✅ VIP подтверждён")

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 <b>VIP {tier['label']} активирован на {tier['days']} дней!</b>\n\n"
                f"Спасибо за поддержку! Используй «🍷 Рассчитать» с льготным курсом раз в день."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента о VIP: {e}")
    await callback.answer("VIP подтверждён!")


@dp.callback_query(F.data.startswith("vipreject_"))
async def admin_vip_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user_id = int(callback.data.split("_")[1])

    await callback.message.edit_text(callback.message.text + "\n\n❌ VIP отклонён")

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Оплата VIP не найдена.</b>\n\n"
                f"Проверь реквизиты: <b>{SBP_PHONE}</b> ({SBP_BANK})\n"
                "Если перевёл — напиши нам напрямую."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента: {e}")
    await callback.answer("VIP отклонён.")


@dp.message(F.text == "🔗 Профиль")
async def profile_section(message: Message):
    user = message.from_user
    username = f"@{user.username}" if user.username else "не указан"

    orders = await get_last_orders(100)
    user_orders = [o for o in orders if o["telegram_id"] == user.id]
    completed = len([o for o in user_orders if o["status"] == "done"])
    total_gold = sum(o["gold_amount"] for o in user_orders if o["status"] == "done")

    text = (
        f"🔗 <b>Профиль</b>\n\n"
        f"👤 Юзернейм: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"✅ Выполненных заказов: <b>{completed}</b>\n"
        f"💰 Всего куплено голды: <b>{total_gold:.0f} G</b>"
    )
    await message.answer(text, parse_mode="HTML")


# ─── Рефералы ────────────────────────────────────────────────────────────────

def get_next_tier(confirmed_count: int, max_tier: int):
    """Найти следующий непройденный порог награды"""
    for threshold, reward in REFERRAL_TIERS:
        if threshold > max_tier:
            return threshold, reward
    return None, None


def get_claimable_tier(confirmed_count: int, max_tier: int):
    """Найти наивысший порог, который уже достигнут, но ещё не выплачен"""
    claimable = None
    for threshold, reward in REFERRAL_TIERS:
        if confirmed_count >= threshold and threshold > max_tier:
            claimable = (threshold, reward)
    return claimable


def referral_claim_kb(tier: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎁 Забрать награду за {tier} друзей", callback_data=f"refclaim_{tier}")]
    ])


@dp.message(F.text == "🤝 Рефералы")
async def referrals_section(message: Message):
    user = message.from_user
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"

    stats = await get_referral_stats(user.id)
    confirmed = stats["confirmed_count"]
    max_tier = stats["max_tier"]

    tiers_text = "\n".join(
        f"{'✅' if confirmed >= t else '👥'} {t} человек — {r} голды"
        for t, r in REFERRAL_TIERS
    )

    text = (
        f"🤝 <b>Рефералы</b>\n\n"
        f"Приглашай друзей по своей ссылке и получай голду! "
        f"Награда засчитывается после первой покупки друга.\n\n"
        f"Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено (подтверждено): <b>{confirmed}</b>\n\n"
        f"{tiers_text}"
    )

    claimable = get_claimable_tier(confirmed, max_tier)
    if claimable:
        threshold, reward = claimable
        await message.answer(text, parse_mode="HTML", reply_markup=referral_claim_kb(threshold))
    else:
        await message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data.startswith("refclaim_"))
async def referral_claim(callback: CallbackQuery):
    tier = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    reward = next((r for t, r in REFERRAL_TIERS if t == tier), None)
    if reward is None:
        await callback.answer("Ошибка тира", show_alert=True)
        return

    stats = await get_referral_stats(user_id)
    if stats["confirmed_count"] < tier or stats["max_tier"] >= tier:
        await callback.answer("Награда недоступна или уже получена.", show_alert=True)
        return

    await mark_tier_claimed(user_id, tier)
    await add_referral_reward(user_id, reward, tier)

    await callback.message.edit_text(
        callback.message.text + f"\n\n🎉 Награда {reward} голды начислена!",
        parse_mode="HTML"
    )
    await callback.answer(f"+{reward} голды начислено!")

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🤝 <b>Реферальная награда выдана</b>\n\n"
                f"👤 Пользователь: <code>{user_id}</code>\n"
                f"🎁 Тир: {tier} друзей → {reward} голды\n\n"
                f"Зачисли голду пользователю вручную в игре."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа о реф. награде: {e}")

@dp.message(F.text == "🍷 Рассчитать")
async def calc_start(message: Message, state: FSMContext):
    await state.set_state(CalcState.waiting_amount)
    await message.answer(
        "🍷 Введи сумму голды чтобы узнать стоимость в рублях:\n\n"
        "Например: <b>2000</b>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )

@dp.message(CalcState.waiting_amount)
async def calc_process(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введи корректное число.")
        return

    buy_price = round(amount * COMMISSION_RATE, 2)
    sell_price = round(amount * SELL_RATE, 2)

    await state.clear()
    await message.answer(
        f"🍷 <b>Расчёт для {amount:.0f} G:</b>\n\n"
        f"➕ Купить: <b>{buy_price:.2f} ₽</b>\n"
        f"⬆️ Продать: получишь <b>{sell_price:.2f} ₽</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ─── Покупка голды ────────────────────────────────────────────────────────────

@dp.message(F.text == "➕ Купить голду")
async def buy_gold_start(message: Message, state: FSMContext):
    await state.set_state(BuyState.waiting_gold_amount)
    await message.answer(
        "💬 Введи сумму голды, которую хочешь купить (только число):\n\n"
        "Например: <b>2000</b>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(BuyState.waiting_gold_amount)
async def buy_process_amount(message: Message, state: FSMContext):
    try:
        gold_amount = float(message.text.replace(",", ".").strip())
        if gold_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введи корректное число.\nНапример: <b>2000</b>", parse_mode="HTML")
        return

    skin_price = round(gold_amount * COMMISSION_RATE, 2)
    await state.update_data(gold_amount=gold_amount, skin_price=skin_price)
    await state.set_state(BuyState.waiting_payment)

    await message.answer(
        f"💰 Сумма к оплате: <b>{gold_amount:.0f} ₽</b>\n\n"
        f"Переведи по СБП:\n"
        f"📱 <b>{SBP_PHONE}</b>\n"
        f"🏦 {SBP_BANK}\n\n"
        f"После оплаты нажми <b>«✅ Оплатил»</b> 👇",
        parse_mode="HTML",
        reply_markup=paid_kb()
    )


@dp.message(BuyState.waiting_payment, F.text == "✅ Оплатил")
async def buy_process_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    gold_amount = data["gold_amount"]
    skin_price = data["skin_price"]

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    order_id = await create_order(
        telegram_id=user.id, username=username,
        gold_amount=gold_amount, skin_price=skin_price
    )

    await state.update_data(order_id=order_id)
    await state.set_state(BuyState.waiting_admin_confirm)

    await message.answer("⏳ Проверяю поступление оплаты...\nОжидай подтверждения (1-5 минут).")

    caption = (
        f"💵 <b>Проверь оплату — заказ #{order_id}</b>\n\n"
        f"👤 Клиент: {username} (id: <code>{user.id}</code>)\n"
        f"💰 Голда: <b>{gold_amount:.0f} G</b>\n"
        f"💵 Сумма: <b>{gold_amount:.0f} ₽</b>\n"
        f"🏷 Цена скина: <b>{skin_price:.2f} G</b>\n\n"
        f"Проверь СБП и подтверди 👇"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=caption, parse_mode="HTML",
                                reply_markup=admin_confirm_kb(order_id, user.id))
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")


@dp.message(BuyState.waiting_payment)
async def buy_payment_wrong(message: Message):
    await message.answer("⏳ Нажми <b>«✅ Оплатил»</b> после перевода.", parse_mode="HTML")


@dp.callback_query(F.data.startswith("confirm_"))
async def admin_confirm_payment(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    parts = callback.data.split("_")
    order_id, user_id = int(parts[1]), int(parts[2])

    await callback.message.edit_text(callback.message.text + "\n\n✅ Оплата подтверждена")

    orders = await get_last_orders(50)
    order = next((o for o in orders if o["id"] == order_id), None)
    skin_price = order["skin_price"] if order else "?"

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"Теперь выставь скин в игре:\n\n"
                f"1️⃣ Скин должен быть <b>Rare</b> с колором (UMP45, P90 и т.д.)\n"
                f"2️⃣ Выставь его на продажу ровно за <b>{skin_price} G</b>\n\n"
                f"3️⃣ Сделай скриншот с видимой <b>аватаркой профиля</b>\n\n"
                f"📸 Пришли скриншот сюда 👇"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента: {e}")
    await callback.answer("Оплата подтверждена!")


@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject_payment(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    parts = callback.data.split("_")
    user_id = int(parts[2])

    await callback.message.edit_text(callback.message.text + "\n\n❌ Оплата отклонена")

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Оплата не найдена.</b>\n\n"
                "Перевод не поступил. Проверь:\n"
                f"• Правильный ли номер: <b>{SBP_PHONE}</b>\n"
                f"• Правильный ли банк: <b>{SBP_BANK}</b>\n\n"
                "Если перевёл — напиши нам напрямую."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента: {e}")
    await callback.answer("Оплата отклонена.")


@dp.message(BuyState.waiting_screenshot, F.photo)
async def buy_process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    gold_amount = data.get("gold_amount")
    skin_price = data.get("skin_price")
    order_id = data.get("order_id")

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    await state.clear()
    await message.answer(
        f"✅ Скриншот получен!\n\n⏳ Покупаю твой скин в течение <b>15 минут</b>.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    photo_id = message.photo[-1].file_id
    caption = (
        f"📸 <b>Скриншот скина — заказ #{order_id}</b>\n\n"
        f"👤 {username} (id: <code>{user.id}</code>)\n"
        f"💰 Голда: <b>{gold_amount:.0f} G</b>\n"
        f"🏷 Цена скина: <b>{skin_price:.2f} G</b>\n\n"
        f"Купи скин → /done {order_id}"
    )
    try:
        await bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить скрин: {e}")


@dp.message(BuyState.waiting_screenshot)
async def buy_screenshot_wrong(message: Message):
    await message.answer("📸 Нужен <b>скриншот</b> (фото) с аватаркой профиля.", parse_mode="HTML")


# ─── Продажа голды (обратная схема) ─────────────────────────────────────────

@dp.message(F.text == "⬆️ Продать голду")
async def sell_gold_start(message: Message, state: FSMContext):
    await state.set_state(SellState.waiting_gold_amount)
    await message.answer(
        "⬆️ Введи сумму голды, которую хочешь продать (только число):\n\n"
        "Например: <b>2000</b>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(SellState.waiting_gold_amount)
async def sell_process_amount(message: Message, state: FSMContext):
    try:
        gold_amount = float(message.text.replace(",", ".").strip())
        if gold_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введи корректное число.\nНапример: <b>2000</b>", parse_mode="HTML")
        return

    payout = round(gold_amount * SELL_RATE, 2)
    await state.update_data(gold_amount=gold_amount, payout=payout)
    await state.set_state(SellState.waiting_card)

    await message.answer(
        f"💰 За <b>{gold_amount:.0f} G</b> ты получишь <b>{payout:.2f} ₽</b>\n\n"
        f"Отправь номер карты или телефона для СБП, куда перевести деньги:",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(SellState.waiting_card)
async def sell_process_card(message: Message, state: FSMContext):
    card = message.text.strip()
    await state.update_data(card=card)
    data = await state.get_data()
    gold_amount = data["gold_amount"]

    await state.set_state(SellState.waiting_screenshot)
    await message.answer(
        f"✅ Реквизиты приняты!\n\n"
        f"Теперь выставь скин на продажу ровно за <b>{gold_amount:.0f} G</b> "
        f"(любой скин на эту сумму).\n\n"
        f"📸 Сделай скриншот с видимой <b>аватаркой профиля</b> и пришли сюда:",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(SellState.waiting_screenshot, F.photo)
async def sell_process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    gold_amount = data.get("gold_amount")
    payout = data.get("payout")
    card = data.get("card")

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    await state.clear()
    await message.answer(
        f"✅ Заявка принята!\n\n"
        f"⏳ Я куплю твой скин в течение <b>15 минут</b> и переведу "
        f"<b>{payout:.2f} ₽</b> на указанные реквизиты.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    photo_id = message.photo[-1].file_id
    caption = (
        f"⬆️ <b>Заявка на продажу голды</b>\n\n"
        f"👤 {username} (id: <code>{user.id}</code>)\n"
        f"💰 Голда: <b>{gold_amount:.0f} G</b>\n"
        f"💵 К выплате: <b>{payout:.2f} ₽</b>\n"
        f"💳 Реквизиты: <code>{card}</code>\n\n"
        f"Купи скин → переведи деньги клиенту"
    )
    try:
        await bot.send_photo(
            chat_id=ADMIN_ID, photo=photo_id, caption=caption, parse_mode="HTML",
            reply_markup=admin_sell_confirm_kb(user.id)
        )
    except Exception as e:
        logger.error(f"Не удалось отправить скрин: {e}")


@dp.message(SellState.waiting_screenshot)
async def sell_screenshot_wrong(message: Message):
    await message.answer("📸 Нужен <b>скриншот</b> (фото) с аватаркой профиля.", parse_mode="HTML")


@dp.callback_query(F.data.startswith("sellconfirm_"))
async def admin_sell_confirm(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user_id = int(callback.data.split("_")[1])

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n✅ Деньги переведены"
    )

    try:
        await bot.send_message(
            chat_id=user_id,
            text="🎉 <b>Готово!</b>\n\nСкин куплен, деньги переведены на твои реквизиты. Спасибо за сделку!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента: {e}")
    await callback.answer("Клиент уведомлён!")


# ─── Хэндлеры администратора ─────────────────────────────────────────────────

@dp.message(Command("done"))
async def cmd_done(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("❌ Формат: /done <id>\nПример: /done 5")
        return

    order_id = int(parts[1])
    order = await complete_order(order_id)
    if not order:
        await message.answer(f"⚠️ Заказ #{order_id} не найден.")
        return

    # Если это первая покупка клиента — подтверждаем реферала, если он был приглашён
    await confirm_referral_if_first_purchase(order["telegram_id"])

    try:
        await bot.send_message(
            chat_id=order["telegram_id"],
            text=f"🎉 <b>Голда зачислена!</b>\n\nЗаказ #{order_id}: {order['gold_amount']:.0f} G\nСпасибо за покупку! 👾",
            parse_mode="HTML"
        )
        await message.answer(f"✅ Заказ #{order_id} выполнен. Клиент уведомлён.")
    except Exception as e:
        await message.answer(f"✅ Заказ #{order_id} выполнен, но не удалось уведомить: {e}")


@dp.message(Command("orders"))
async def cmd_orders(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    orders = await get_last_orders(10)
    if not orders:
        await message.answer("📋 Заказов пока нет.")
        return

    lines = ["📋 <b>Последние заказы:</b>\n"]
    for o in orders:
        status_emoji = "✅" if o["status"] == "done" else "⏳"
        lines.append(f"{status_emoji} <b>#{o['id']}</b> | {o['username']} | {o['gold_amount']:.0f}G | {o['created_at']}")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── Обработка данных из Mini App (Web App) ──────────────────────────────────

import json as _json
import hmac as _hmac
import hashlib as _hashlib
from urllib.parse import parse_qsl as _parse_qsl


def verify_webapp_init_data(init_data: str, bot_token: str) -> bool:
    """
    Проверяет HMAC-подпись initData, которую Telegram передаёт Mini App.
    Без этой проверки нельзя доверять данным (user.id и т.д.) из Web App —
    initDataUnsafe в JS называется "Unsafe" не просто так.

    Алгоритм по официальной документации Telegram:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    if not init_data:
        return False

    try:
        parsed = dict(_parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return False

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return False

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = _hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=_hashlib.sha256
    ).digest()

    computed_hash = _hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=_hashlib.sha256
    ).hexdigest()

    return _hmac.compare_digest(computed_hash, received_hash)


# Соответствие action из Mini App -> текст той же кнопки в обычном меню
WEBAPP_ACTION_MAP = {
    "buy": "➕ Купить голду",
    "sell": "⬆️ Продать голду",
    "withdraw": "📈 Вывести голду",
    "calc": "🍷 Рассчитать",
    "profile": "🔗 Профиль",
    "games": "💼 Игры",
    "referrals": "🤝 Рефералы",
    "reviews": "📊 Отзывы",
    "vip": "👑 VIP",
    "promo": "🎫 Промокод",
    "bonus": "🎁 Бонус",
    "about": "🍷 О нас",
}


# Соответствие action из Mini App -> функция-хендлер, которая обрабатывает ту же кнопку в обычном меню
WEBAPP_HANDLER_MAP = {
    "buy": buy_gold_start,
    "sell": sell_gold_start,
    "withdraw": lambda msg, st: msg.answer(STUB_TEXTS["📈 Вывести голду"], parse_mode="HTML"),
    "calc": calc_start,
    "profile": lambda msg, st: profile_section(msg),
    "games": lambda msg, st: games_section(msg),
    "referrals": lambda msg, st: referrals_section(msg),
    "reviews": lambda msg, st: msg.answer(STUB_TEXTS["📊 Отзывы"], parse_mode="HTML"),
    "vip": vip_section,
    "promo": lambda msg, st: msg.answer(STUB_TEXTS["🎫 Промокод"], parse_mode="HTML"),
    "bonus": lambda msg, st: msg.answer(STUB_TEXTS["🎁 Бонус"], parse_mode="HTML"),
    "about": lambda msg, st: about_section(msg),
}


@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message, state: FSMContext):
    """
    Когда пользователь нажимает кнопку в Mini App, Telegram присылает
    сюда message.web_app_data.data — JSON вида {"action": "buy", "init_data": "..."}.
    Сначала проверяем подпись init_data — если она не совпадает, данным не доверяем
    и просто игнорируем запрос.
    """
    try:
        payload = _json.loads(message.web_app_data.data)
    except Exception:
        await message.answer("⚠️ Не удалось обработать данные из терминала.")
        return

    init_data = payload.get("init_data", "")
    if not verify_webapp_init_data(init_data, BOT_TOKEN):
        logger.warning(f"Webapp init_data verification failed for user {message.from_user.id}")
        await message.answer("⚠️ Не удалось подтвердить подлинность запроса. Попробуй заново открыть терминал.")
        return

    action = payload.get("action")

    # set_currency используется только внутри Mini App для подсветки кнопки — игнорируем здесь
    if action == "set_currency" or action is None:
        return

    mapped_text = WEBAPP_ACTION_MAP.get(action)
    if not mapped_text:
        await message.answer("⚠️ Неизвестное действие из терминала.")
        return

    # Напрямую вызываем тот же хендлер, что обрабатывает соответствующую reply-кнопку.
    # Простая диспетчеризация по тексту — без хрупких внутренних API aiogram.
    handler = WEBAPP_HANDLER_MAP.get(action)
    if handler:
        await handler(message, state)
    else:
        await message.answer(f"{mapped_text}\n\n(используй обычное меню ниже 👇)")


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
