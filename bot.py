import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_ID, COMMISSION_RATE
from database import init_db, create_order, complete_order, get_last_orders

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ─── Состояния FSM ───────────────────────────────────────────────────────────

class OrderState(StatesGroup):
    waiting_gold_amount = State()   # Ждём ввода суммы голды
    waiting_screenshot = State()    # Ждём скриншот со скином


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="💰 Купить голду")]],
        resize_keyboard=True
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    """Кнопка отмены во время диалога"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить заказ")]],
        resize_keyboard=True
    )


# ─── Хэндлеры клиента ────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот для покупки внутриигровой голды.\n\n"
        "Нажми кнопку ниже чтобы оформить заказ 👇",
        reply_markup=main_menu()
    )


@dp.message(F.text == "💰 Купить голду")
async def buy_gold_start(message: Message, state: FSMContext):
    """Начало оформления заказа"""
    await state.set_state(OrderState.waiting_gold_amount)
    await message.answer(
        "💬 Введи сумму голды, которую хочешь купить (только число):\n\n"
        "Например: <b>2000</b>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(F.text == "❌ Отменить заказ")
async def cancel_order(message: Message, state: FSMContext):
    """Отмена заказа на любом этапе"""
    await state.clear()
    await message.answer(
        "❌ Заказ отменён.",
        reply_markup=main_menu()
    )


@dp.message(OrderState.waiting_gold_amount)
async def process_gold_amount(message: Message, state: FSMContext):
    """Обрабатываем введённую сумму голды"""
    # Проверяем что ввели число
    try:
        gold_amount = float(message.text.replace(",", ".").strip())
        if gold_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Пожалуйста, введи корректное число.\n"
            "Например: <b>2000</b>",
            parse_mode="HTML"
        )
        return

    # Считаем цену скина с комиссией
    skin_price = round(gold_amount * COMMISSION_RATE, 2)

    # Сохраняем в состояние
    await state.update_data(gold_amount=gold_amount, skin_price=skin_price)
    await state.set_state(OrderState.waiting_screenshot)

    await message.answer(
        f"✅ Отлично! Вот инструкция:\n\n"
        f"1️⃣ Выставь скин на продажу ровно за <b>{skin_price:.2f} G</b>\n"
        f"   (это {gold_amount:.0f} G голды + 20% комиссия)\n\n"
        f"2️⃣ Скин должен быть <b>Rare</b> с колором (например, UMP45 или P90 с номером)\n\n"
        f"3️⃣ Сделай скриншот страницы скина с видимой <b>аватаркой профиля</b> "
        f"(чтобы я нашёл твой лот в маркете)\n\n"
        f"📸 Пришли скриншот сюда 👇",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(OrderState.waiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    """Получаем скриншот и оформляем заказ"""
    data = await state.get_data()
    gold_amount = data["gold_amount"]
    skin_price = data["skin_price"]

    # Данные клиента
    user = message.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    # Сохраняем заказ в БД
    order_id = await create_order(
        telegram_id=user.id,
        username=username,
        gold_amount=gold_amount,
        skin_price=skin_price
    )

    # Уведомляем клиента
    await state.clear()
    await message.answer(
        f"✅ Заявка #{order_id} принята!\n\n"
        f"⏳ Ожидай покупки скина в течение <b>15 минут</b>.\n"
        f"После покупки ты получишь уведомление.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    # Отправляем уведомление администратору
    photo_id = message.photo[-1].file_id  # Берём фото наилучшего качества
    caption = (
        f"🔔 <b>Новый заказ #{order_id}</b>\n\n"
        f"👤 Клиент: {username} (id: <code>{user.id}</code>)\n"
        f"💰 Голда: <b>{gold_amount:.0f} G</b>\n"
        f"🏷 Цена скина: <b>{skin_price:.2f} G</b>\n\n"
        f"Когда выкупишь — отправь: /done {order_id}"
    )

    try:
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=caption,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")


@dp.message(OrderState.waiting_screenshot)
async def process_screenshot_wrong(message: Message):
    """Клиент прислал не фото"""
    await message.answer(
        "📸 Нужен именно <b>скриншот</b> (фото), а не текст.\n"
        "Пожалуйста, пришли скриншот с аватаркой профиля.",
        parse_mode="HTML"
    )


# ─── Хэндлеры администратора ─────────────────────────────────────────────────

@dp.message(Command("done"))
async def cmd_done(message: Message):
    """Пометить заказ выполненным: /done <id>"""
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("❌ Формат: /done <id заказа>\nПример: /done 5")
        return

    order_id = int(parts[1])
    order = await complete_order(order_id)

    if not order:
        await message.answer(f"⚠️ Заказ #{order_id} не найден.")
        return

    # Уведомляем клиента что голда зачислена
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
        await message.answer(f"✅ Заказ #{order_id} выполнен, но не удалось уведомить клиента: {e}")


@dp.message(Command("orders"))
async def cmd_orders(message: Message):
    """Показать последние 10 заказов: /orders"""
    if message.from_user.id != ADMIN_ID:
        return

    orders = await get_last_orders(10)

    if not orders:
        await message.answer("📋 Заказов пока нет.")
        return

    # Форматируем список
    lines = ["📋 <b>Последние заказы:</b>\n"]
    for o in orders:
        status_emoji = "✅" if o["status"] == "done" else "⏳"
        lines.append(
            f"{status_emoji} <b>#{o['id']}</b> | {o['username']} | "
            f"{o['gold_amount']:.0f}G | {o['skin_price']:.2f}G | {o['created_at']}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
