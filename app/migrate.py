import aiosqlite
import asyncio
from config import Config

async def migrate():
    db = await aiosqlite.connect(Config.DB_NAME)
    # Создание таблиц, если их нет
    tables = {
        "users": """
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
        """,
        "entries": """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, period_start)
            );
        """,
        "reports": """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, year, month)
            );
        """,
        "auto_reports": """
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
        """,
        "broadcasts": """
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """
    }
    for name, ddl in tables.items():
        await db.execute(ddl)
        print(f"Table {name} ready.")

    # Добавление колонок, если их нет
    cursor = await db.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in await cursor.fetchall()]

    if 'timezone' not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC'")
        print("Added column timezone to users.")
    if 'custom_days' not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN custom_days TEXT DEFAULT ''")
        print("Added column custom_days to users.")
    if 'vacation_until' not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN vacation_until TEXT DEFAULT NULL")
        print("Added column vacation_until to users.")

     # Заполнение custom_days значением по умолчанию для всех пользователей
    await db.execute("UPDATE users SET custom_days='0,1,2,3,4,5,6' WHERE custom_days IS NULL OR custom_days=''")
    print("Updated custom_days for existing users.")
    
    # Автоматическое уведомление об обновлении (только если ещё не было)
    cursor = await db.execute("SELECT id FROM broadcasts WHERE message_text LIKE 'Бот обновлён до версии 1%'")
    if not await cursor.fetchone():
        from version import VERSION  # предполагаем, что версия доступна
        update_message = (
            f"Бот обновлён до версии {VERSION}!\n\n"
            "Что нового:\n"
            "- Гибкое расписание напоминаний (выбор конкретных дней недели)\n"
            "- Режим отпуска (/vacation)\n"
            "- Автоматические еженедельные и ежемесячные отчёты\n"
            "- Улучшенная стабильность и логирование\n\n"
            "Посмотрите /set_reminder и /set_auto_report, чтобы настроить уведомления."
        )
        await db.execute("INSERT INTO broadcasts (message_text) VALUES (?)", (update_message,))
        print("Added update broadcast message.")

    await db.commit()
    await db.close()
    print("Migration completed.")

if __name__ == "__main__":
    asyncio.run(migrate())