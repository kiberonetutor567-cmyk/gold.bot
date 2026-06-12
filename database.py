import aiosqlite
from datetime import datetime

DB_PATH = "orders.db"


async def init_db():
    """Создаём таблицу заказов если её нет"""
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
        # Получаем заказ перед изменением
        async with db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
        
        if not row:
            return None
        
        # Помечаем выполненным
        await db.execute(
            "UPDATE orders SET status = 'done' WHERE id = ?",
            (order_id,)
        )
        await db.commit()
        
        return {
            "id": row[0],
            "telegram_id": row[1],
            "username": row[2],
            "gold_amount": row[3],
            "skin_price": row[4],
            "status": row[5],
            "created_at": row[6],
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
            "id": row[0],
            "telegram_id": row[1],
            "username": row[2],
            "gold_amount": row[3],
            "skin_price": row[4],
            "status": row[5],
            "created_at": row[6],
        })
    return result
