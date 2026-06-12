import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота из .env файла
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram ID администратора (ты)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Комиссия (20% = 1.20)
COMMISSION_RATE = 1.20
