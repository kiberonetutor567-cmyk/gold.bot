import aiosqlite
from datetime import datetime

DB_PATH = "orders.db"


async def init_db():
    """Создаём таблицы если их нет"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                gold_amount REAL NOT NULL,
                skin_price REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)

        # Таблица рефералов: кто кого привёл
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                confirmed INTEGER DEFAULT 0,
                reward_claimed_tier INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # Реферальные начисления (история выплат голды)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                gold_amount REAL NOT NULL,
                tier INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def create_order(telegram_id: int, username: str, gold_amount: float, skin_price: float) -> int:
    """Создать новый заказ, вернуть его ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (telegram_id, username, gold_amount, skin_price, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (telegram_id, username, gold_amount, skin_price, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
        return cursor.lastrowid


async def complete_order(order_id: int) -> dict | None:
    """Пометить заказ выполненным, вернуть данные заказа"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None

        await db.execute("UPDATE orders SET status = 'done' WHERE id = ?", (order_id,))
        await db.commit()

        return {
            "id": row[0], "telegram_id": row[1], "username": row[2],
            "gold_amount": row[3], "skin_price": row[4],
            "status": row[5], "created_at": row[6],
        }


async def get_last_orders(limit: int = 10) -> list[dict]:
    """Получить последние N заказов"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()

    result = []
    for row in rows:
        result.append({
            "id": row[0], "telegram_id": row[1], "username": row[2],
            "gold_amount": row[3], "skin_price": row[4],
            "status": row[5], "created_at": row[6],
        })
    return result


# ─── Реферальная система ─────────────────────────────────────────────────────

async def add_referral(referrer_id: int, referred_id: int) -> bool:
    """
    Зарегистрировать что referred_id пришёл по ссылке referrer_id.
    Возвращает False если этот пользователь уже зарегистрирован как реферал (любой),
    или если он пытается пригласить сам себя.
    """
    if referrer_id == referred_id:
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем что этот юзер ещё не привязан ни к кому
        async with db.execute(
            "SELECT id FROM referrals WHERE referred_id = ?", (referred_id,)
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            return False

        await db.execute(
            """
            INSERT INTO referrals (referrer_id, referred_id, confirmed, reward_claimed_tier, created_at)
            VALUES (?, ?, 0, 0, ?)
            """,
            (referrer_id, referred_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
        return True


async def confirm_referral_if_first_purchase(user_id: int):
    """
    Вызывается когда у user_id завершается (done) первый заказ.
    Если user_id был кем-то приглашён и ещё не подтверждён — подтверждаем.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Сколько у юзера завершённых заказов
        async with db.execute(
            "SELECT COUNT(*) FROM orders WHERE telegram_id = ? AND status = 'done'",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        completed_count = row[0] if row else 0

        # Подтверждаем реферала только если это была его первая покупка
        if completed_count == 1:
            await db.execute(
                "UPDATE referrals SET confirmed = 1 WHERE referred_id = ? AND confirmed = 0",
                (user_id,)
            )
            await db.commit()


async def get_referral_stats(referrer_id: int) -> dict:
    """Сколько подтверждённых рефералов у пользователя, и какой реферальный тир уже выплачен"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND confirmed = 1",
            (referrer_id,)
        ) as cursor:
            row = await cursor.fetchone()
        confirmed_count = row[0] if row else 0

        async with db.execute(
            "SELECT MAX(reward_claimed_tier) FROM referrals WHERE referrer_id = ?",
            (referrer_id,)
        ) as cursor:
            row = await cursor.fetchone()
        max_tier = row[0] if row and row[0] else 0

    return {"confirmed_count": confirmed_count, "max_tier": max_tier}


async def mark_tier_claimed(referrer_id: int, tier: int):
    """Отметить что награда за tier (20/50/100 друзей) выдана этому пользователю"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE referrals SET reward_claimed_tier = ? WHERE referrer_id = ?",
            (tier, referrer_id)
        )
        await db.commit()


async def add_referral_reward(user_id: int, gold_amount: float, tier: int):
    """Записать выданную реферальную награду в историю"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO referral_rewards (user_id, gold_amount, tier, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, gold_amount, tier, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
