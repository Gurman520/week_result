import aiosqlite
from config import Config
from datetime import datetime, timedelta


DB_NAME = Config.DB_NAME

def _row_to_dict(cursor, row):
    """Преобразует кортеж строки в словарь, используя описание курсора."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                frequency TEXT DEFAULT 'week',
                day_of_week INTEGER DEFAULT 4,
                day_of_month INTEGER DEFAULT 1,
                time_hour INTEGER DEFAULT 18,
                time_minute INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'UTC',
                custom_days TEXT DEFAULT '',
                vacation_until TEXT DEFAULT NULL,
                active INTEGER DEFAULT 1
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, period_start)
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'month',  -- 'week' или 'month'
                period_start TEXT NOT NULL,                -- первый день периода (YYYY-MM-DD)
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, period_start, report_type)
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_reports (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                frequency TEXT DEFAULT 'month',
                day_of_week INTEGER DEFAULT 0,
                day_of_month INTEGER DEFAULT 1,
                time_hour INTEGER DEFAULT 9,
                time_minute INTEGER DEFAULT 0,
                last_run TIMESTAMP
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return _row_to_dict(cursor, row) if row else None

async def upsert_user(user_id: int, **kwargs):
    columns = ', '.join(kwargs.keys())
    placeholders = ', '.join(['?'] * len(kwargs))
    updates = ', '.join(f"{k} = excluded.{k}" for k in kwargs)
    values = list(kwargs.values())
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            f"INSERT INTO users (user_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}",
            [user_id] + values
        )
        await db.commit()

async def set_user_timezone(user_id: int, tz_str: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz_str, user_id))
        await db.commit()

async def set_vacation(user_id: int, days: int):
    vacation_until = None
    if days > 0:
        vacation_until = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET vacation_until=? WHERE user_id=?", (vacation_until, user_id))
        await db.commit()

async def save_entry(user_id: int, content: str, period_start: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO entries (user_id, period_start, content) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, period_start) DO UPDATE SET content=excluded.content",
            (user_id, period_start, content)
        )
        await db.commit()

async def get_entries_for_month(user_id: int, year: int, month: int):
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year+1}-01-01"
    else:
        end_date = f"{year}-{month+1:02d}-01"
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT content FROM entries WHERE user_id=? AND period_start >= ? AND period_start < ?",
            (user_id, start_date, end_date)
        )
        return [row[0] for row in await cursor.fetchall() if row[0]]

async def save_report(user_id: int, report_type: str, period_start: str, content: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reports (user_id, report_type, period_start, content) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, period_start, report_type) DO UPDATE SET content=excluded.content",
            (user_id, report_type, period_start, content)
        )
        await db.commit()

async def get_user_reports(user_id: int):
    """Возвращает список (id, report_type, period_start) для пользователя, отсортированный от новых к старым."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT id, report_type, period_start FROM reports WHERE user_id=? ORDER BY period_start DESC",
            (user_id,)
        )
        return await cursor.fetchall()

async def get_report_by_id(report_id: int):
    """Возвращает полный отчёт по ID, если он принадлежит пользователю (проверка в хендлере)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id, report_type, period_start, content FROM reports WHERE id=?",
            (report_id,)
        )
        return await cursor.fetchone()

async def get_all_active_users():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE active = 1")
        rows = await cursor.fetchall()
        return [_row_to_dict(cursor, row) for row in rows]

async def get_auto_report_configs():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM auto_reports WHERE enabled=1")
        return await cursor.fetchall()

async def get_entries_for_dates(user_id: int, start_date: str, end_date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT content FROM entries WHERE user_id=? AND period_start >= ? AND period_start < ?",
            (user_id, start_date, end_date)
        )
        return [row[0] for row in await cursor.fetchall() if row[0]]

async def create_broadcast(text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO broadcasts (message_text) VALUES (?)", (text,))
        await db.commit()

async def get_pending_broadcasts():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id, message_text FROM broadcasts WHERE status='pending'")
        return await cursor.fetchall()

async def mark_broadcast_sent(broadcast_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE broadcasts SET status='sent' WHERE id=?", (broadcast_id,))
        await db.commit()
