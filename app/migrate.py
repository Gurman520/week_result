import aiosqlite
import asyncio
import sys
from config import Config

async def migrate(era):
    try:
        db = await aiosqlite.connect(Config.DB_NAME)
        if era == 1:
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
            cursor = await db.execute("SELECT id FROM broadcasts WHERE message_text LIKE 'Бот обновлён до версии 2.0.0!%'")
            if not await cursor.fetchone():
                from version import VERSION  # предполагаем, что версия доступна
                update_message = (
                    f"Бот обновлён до версии {VERSION}!\n\n"
                    "Что нового:\n"
                    "- Исправлен баг с запуском процессов в раз неделю из-за расхождения дней недели\n"
                    "Посмотрите /set_reminder и /set_auto_report, чтобы настроить уведомления."
                )
                await db.execute("INSERT INTO broadcasts (message_text) VALUES (?)", (update_message,))
                print("Added update broadcast message.")

            # Конвертация дней недели из старого формата (0=пн) в новый (0=вс)
            # Применяем ко всем пользователям, у которых day_of_week не равен NULL
            await db.execute("UPDATE users SET day_of_week = (day_of_week + 1) % 7 WHERE day_of_week IS NOT NULL")
            await db.execute("UPDATE auto_reports SET day_of_week = (day_of_week + 1) % 7 WHERE day_of_week IS NOT NULL")
            print("Converted day_of_week to new format (0=Sun).")
        elif era == 2:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reports'")
            has_reports = await cursor.fetchone() is not None

            if has_reports:
                # Получаем список колонок старой таблицы
                cursor = await db.execute("PRAGMA table_info(reports)")
                old_columns = [row[1] for row in await cursor.fetchall()]
                print(f"Old reports columns: {old_columns}")

                # Переименовываем старую таблицу
                await db.execute("ALTER TABLE reports RENAME TO reports_old")
                print("Renamed reports to reports_old")

                # Создаём новую таблицу
                await db.execute("""
                    CREATE TABLE reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        report_type TEXT NOT NULL DEFAULT 'month',
                        period_start TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, period_start, report_type)
                    );
                """)
                print("Created new reports table")

                # Перенос данных, если в старой таблице были нужные колонки
                if 'user_id' in old_columns and 'year' in old_columns and 'month' in old_columns:
                    await db.execute("""
                        INSERT INTO reports (user_id, report_type, period_start, content, created_at)
                        SELECT user_id, 'month',
                            printf('%04d-%02d-01', year, month),
                            content, created_at
                        FROM reports_old
                    """)
                    print("Migrated old monthly reports")
                else:
                    print("Old reports table does not have expected columns, skipping data migration")

                # Удаляем старую таблицу
                await db.execute("DROP TABLE IF EXISTS reports_old")
                await db.commit()
                print("Old reports table dropped")
            else:
                # Таблицы reports нет, создаём новую
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        report_type TEXT NOT NULL DEFAULT 'month',
                        period_start TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, period_start, report_type)
                    );
                """)
                print("Created reports table (no old data to migrate)")
            
            # Автоматическое уведомление об обновлении (только если ещё не было)
            cursor = await db.execute("SELECT id FROM broadcasts WHERE message_text LIKE 'Бот обновлён до версии 2.1.0!%'")
            if not await cursor.fetchone():
                from version import VERSION  # предполагаем, что версия доступна
                update_message = (
                    f"Бот обновлён до версии {VERSION}!\n\n"
                    "Что нового:\n"
                    "- Исправлеена ошибка при формировании еженедельного отчета\n"
                    "- Добавлены сообщения при возникновении ошибок в работе бота."
                )
                await db.execute("INSERT INTO broadcasts (message_text) VALUES (?)", (update_message,))
                print("Added update broadcast message.")

        await db.commit()
        print("Migration completed.")
    
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        if db:
            await db.rollback()
        sys.exit(1)
    finally:
        if db:
            await db.close()

if __name__ == "__main__":
    print("Запущен скрипт Миграции ...\n" \
    "Доступные эпохи миграции:\n" \
    "1. Миграция v.1 - добавление и обновление колонок для работы проекта v. 2.0.1\n" \
    "2. Миграция v.2 - Расширение таблицы отчетов, для более гибкой системы хранения")
    era = int(input("Выберете эпоху миграции: "))
    asyncio.run(migrate(era))