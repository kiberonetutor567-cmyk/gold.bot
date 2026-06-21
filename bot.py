import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_ID, COMMISSION_RATE
from database import init_db, create_order, complete_order, get_last_orders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─── Твои реквизиты и инфо ───────────────────────────────────────────────────
SBP_PHONE = "+7XXXXXXXXXX"      # ← ВСТАВЬ СВОЙ НОМЕР
SBP_BANK = "Сбер/Тинькофф"     # ← ВСТАВЬ СВОЙ БАНК
OWNER_CONTACT = "@твой_юзернейм"  # ← ВСТАВЬ СВОЙ ЮЗЕРНЕЙМ
REVIEWS_CHAT_LINK = "https://t.me/+xxxxxxx"  # ← ВСТАВЬ ССЫЛКУ НА ОТЗЫВЫ (если есть)


# ─── Состояния FSM ───────────────────────────────────────────────────────────

class OrderState(StatesGroup):
    waiting_gold_amount = State()
    waiting_payment = State()
    waiting_admin_confirm = State()
    waiting_screenshot = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def main_menu_inline():
    """Главное инлайн-меню с разделами"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Купить голду", callback_data="menu_buy"),
        ],
        [
            InlineKeyboardButton(text="🔗 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="🎮 Игры", callback_data="menu_games"),
        ],
        [
            InlineKeyboardButton(text="🤝 Рефералы", callback_data="menu_referrals"),
            InlineKeyboardButton(text="📈 Отзывы", callback_data="menu_reviews"),
        ],
        [
            InlineKeyboardButton(text="❓ Как это работает", callback_data="menu_faq"),
        ],
        [
            InlineKeyboardButton(text="ℹ️ О нас", callback_data="menu_about"),
        ],
    ])


def back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu_back")]
    ])


def paid_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Оплатил")],
            [KeyboardButton(text="❌ Отменить заказ")]
        ],
        resize_keyboard=True
    )

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить заказ")]],
        resize_keyboard=True
    )

def admin_confirm_kb(order_id: int, user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Оплата получена",
                callback_data=f"confirm_{order_id}_{user_id}"
            ),
            InlineKeyboardButton(
                text="❌ Не получена",
                callback_data=f"reject_{order_id}_{user_id}"
            )
        ]
    ])


# ─── Тексты разделов ─────────────────────────────────────────────────────────

FAQ_TEXT = (
    "❓ <b>Как это работает?</b>\n\n"
    "1️⃣ Ты выбираешь сколько голды хочешь купить\n"
    "2️⃣ Переводишь рубли по СБП (1 голда = 1 рубль)\n"
    "3️⃣ Выставляешь скин на продажу в игре\n"
    "4️⃣ Я покупаю твой скин — голда приходит тебе\n\n"
    "💡 <b>Почему скин?</b>\n"
    "В Standoff 2 нельзя передать голду напрямую. "
    "Скин — это способ сделать обмен через маркет.\n\n"
    "⏱ <b>Как быстро?</b>\n"
    "Обычно в течение 15 минут после оплаты.\n\n"
    "🔒 <b>Безопасно ли?</b>\n"
    "Сначала ты платишь рублями, потом я покупаю скин. "
    "Никакого риска с твоей стороны."
)

ABOUT_TEXT = (
    "ℹ️ <b>О нас</b>\n\n"
    "👋 Привет! Я бот для покупки внутриигровой голды в Standoff 2.\n"
    "💱 Быстрый обмен и честные расчёты\n"
    "⚡ Работаю каждый день\n"
    f"✉️ Связь с владельцем: {OWNER_CONTACT}"
)

GAMES_TEXT = (
    "🎮 <b>Поддерживаемые игры</b>\n\n"
    "• Standoff 2 — голда\n\n"
    "Скоро добавим больше игр!"
)

REFERRALS_TEXT = (
    "🤝 <b>Реферальная программа</b>\n\n"
    "Скоро здесь появится твоя реферальная ссылка "
    "и бонусы за приглашённых друзей!"
)


# ─── /start и главное меню ───────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏠 <b>Вы в главном меню</b>\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_inline()
    )


@dp.callback_query(F.data == "menu_back")
async def menu_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Вы в главном меню</b>\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_inline()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_faq")
async def menu_faq(callback: CallbackQuery):
    await callback.message.edit_text(
        FAQ_TEXT, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_about")
async def menu_about(callback: CallbackQuery):
    await callback.message.edit_text(
        ABOUT_TEXT, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_games")
async def menu_games(callback: CallbackQuery):
    await callback.message.edit_text(
        GAMES_TEXT, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_referrals")
async def menu_referrals(callback: CallbackQuery):
    await callback.message.edit_text(
        REFERRALS_TEXT, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_reviews")
async def menu_reviews(callback: CallbackQuery):
    text = "📈 <b>Отзывы</b>\n\n"
    if REVIEWS_CHAT_LINK and "xxxxxxx" not in REVIEWS_CHAT_LINK:
        text += f"Все отзывы клиентов здесь: {REVIEWS_CHAT_LINK}"
    else:
        text += "Раздел отзывов скоро будет доступен!"
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: CallbackQuery):
    user = callback.from_user
    username = f"@{user.username}" if user.username else "не указан"

    orders = await get_last_orders(50)
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
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=back_to_menu_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_buy")
async def menu_buy(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderState.waiting_gold_amount)
    await callback.message.edit_text(
        "💬 Введи сумму голды, которую хочешь купить (только число):\n\n"
        "Например: <b>2000</b>",
        parse_mode="HTML"
    )
    await callback.answer()


# ─── Покупка голды ────────────────────────────────────────────────────────────

@dp.message(F.text == "❌ Отменить заказ")
async def cancel_order(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Заказ отменён.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[]], resize_keyboard=True)
    )
    await message.answer(
        "🏠 <b>Главное меню</b>\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_inline()
    )


@dp.message(OrderState.waiting_gold_amount)
async def process_gold_amount(message: Message, state: FSMContext):
    try:
        gold_amount = float(message.text.replace(",", ".").strip())
        if gold_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Пожалуйста, введи корректное число.\nНапример: <b>2000</b>",
            parse_mode="HTML"
        )
        return

    skin_price = round(gold_amount * COMMISSION_RATE, 2)
    await state.update_data(gold_amount=gold_amount, skin_price=skin_price)
    await state.set_state(OrderState.waiting_payment)

    await message.answer(
        f"💰 Сумма к оплате: <b>{gold_amount:.0f} ₽</b>\n\n"
        f"Переведи по СБП:\n"
        f"📱 <b>{SBP_PHONE}</b>\n"
        f"🏦 {SBP_BANK}\n\n"
        f"После оплаты нажми <b>«✅ Оплатил»</b> 👇",
        parse_mode="HTML",
        reply_markup=paid_kb()
    )


@dp.message(OrderState.waiting_payment, F.text == "✅ Оплатил")
async def process_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    gold_amount = data["gold_amount"]
    skin_price = data["skin_price"]

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    order_id = await create_order(
        telegram_id=user.id,
        username=username,
        gold_amount=gold_amount,
        skin_price=skin_price
    )

    await state.update_data(order_id=order_id)
    await state.set_state(OrderState.waiting_admin_confirm)

    await message.answer(
        "⏳ Проверяю поступление оплаты...\n"
        "Ожидай подтверждения (обычно 1-5 минут)."
    )

    caption = (
        f"💵 <b>Проверь оплату — заказ #{order_id}</b>\n\n"
        f"👤 Клиент: {username} (id: <code>{user.id}</code>)\n"
        f"💰 Голда: <b>{gold_amount:.0f} G</b>\n"
        f"💵 Сумма: <b>{gold_amount:.0f} ₽</b>\n"
        f"🏷 Цена скина: <b>{skin_price:.2f} G</b>\n\n"
        f"Проверь СБП и подтверди 👇"
    )

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=caption,
            parse_mode="HTML",
            reply_markup=admin_confirm_kb(order_id, user.id)
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")


@dp.message(OrderState.waiting_payment)
async def process_payment_wrong(message: Message):
    await message.answer(
        "⏳ Нажми <b>«✅ Оплатил»</b> после того как сделал перевод.",
        parse_mode="HTML"
    )


# ─── Кнопки подтверждения/отклонения для админа ──────────────────────────────

@dp.callback_query(F.data.startswith("confirm_"))
async def admin_confirm_payment(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    parts = callback.data.split("_")
    order_id = int(parts[1])
    user_id = int(parts[2])

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Оплата подтверждена",
    )

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
            parse_mode="HTML",
            reply_markup=cancel_kb()
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

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Оплата отклонена",
    )

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


@dp.message(OrderState.waiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    gold_amount = data.get("gold_amount")
    skin_price = data.get("skin_price")
    order_id = data.get("order_id")

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    await state.clear()
    await message.answer(
        f"✅ Скриншот получен!\n\n"
        f"⏳ Покупаю твой скин в течение <b>15 минут</b>.\n"
        f"Ты получишь уведомление когда голда зачислена.",
        parse_mode="HTML"
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
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=caption,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить скрин: {e}")


@dp.message(OrderState.waiting_screenshot)
async def process_screenshot_wrong(message: Message):
    await message.answer(
        "📸 Нужен <b>скриншот</b> (фото) с аватаркой профиля.",
        parse_mode="HTML"
    )


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

    try:
        await bot.send_message(
            chat_id=order["telegram_id"],
            text=(
                f"🎉 <b>Голда зачислена!</b>\n\n"
                f"Заказ #{order_id}: {order['gold_amount']:.0f} G\n"
                f"Спасибо за покупку! 👾"
            ),
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
        lines.append(
            f"{status_emoji} <b>#{o['id']}</b> | {o['username']} | "
            f"{o['gold_amount']:.0f}G | {o['created_at']}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
