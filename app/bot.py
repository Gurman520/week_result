import asyncio
import time
import logging
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from telegram.error import NetworkError, TimedOut
from config import Config
from database import init_db
from scheduler import restore_reminders, restore_auto_reports
from broadcast import send_broadcasts
from logger_setup import setup_logging
from handlers.start import start, set_timezone_callback, change_timezone_command, change_timezone_callback, vacation_command
from handlers.reminders import reminder_conv
from handlers.auto_reports import auto_report_conv
from handlers.reports import summary, list_reports, view_report
from handlers.messages import message_handler
from handlers.debug import debug_time_handler, debug_jobs_handler
from handlers.broadcast import broadcast_command
from version import VERSION



logger = logging.getLogger(__name__)

logger.info(f"Starting bot version {VERSION}")

async def set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Регистрация и начало работы"),
        BotCommand("set_reminder", "Настроить напоминания"),
        BotCommand("set_auto_report", "Настроить автоматический отчёт"),
        BotCommand("reports", "Список сохранённых отчётов"),
        BotCommand("report", "Показать конкретный отчёт"),
        BotCommand("time", "Текущее время системы"),
        BotCommand("jobs", "Активные задачи (отладка)"),
        BotCommand("set_timezone", "Изменить часовой пояс"),
        BotCommand("vacation", "Включить режим отпуска"),
    ]
    await application.bot.set_my_commands(commands)

async def post_init(application: Application):
    await restore_reminders(application)
    await restore_auto_reports(application)
    await set_bot_commands(application)
    # Запускаем рассылку в фоне, чтобы не блокировать запуск
    asyncio.create_task(send_broadcasts(application))

def main():
    setup_logging()
    asyncio.run(init_db())

    app = Application.builder() \
        .token(Config.BOT_TOKEN) \
        .connect_timeout(30) \
        .read_timeout(30) \
        .write_timeout(30) \
        .pool_timeout(30) \
        .post_init(post_init) \
        .build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_timezone", change_timezone_command))
    app.add_handler(CommandHandler("vacation", vacation_command))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("reports", list_reports))
    app.add_handler(CommandHandler("report", view_report))

    # Админские команды
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(debug_time_handler)
    app.add_handler(debug_jobs_handler)


    # Callback'и для таймзоны
    app.add_handler(CallbackQueryHandler(set_timezone_callback, pattern="^tz="))
    app.add_handler(CallbackQueryHandler(change_timezone_callback, pattern="^tz_change="))

    # Настройки автоматики
    app.add_handler(reminder_conv)
    app.add_handler(auto_report_conv)

    # Текстовые сообщения
    app.add_handler(message_handler)

    # Бесконечный перезапуск при сетевых ошибках
    while True:
        try:
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False
            )
        except (NetworkError, TimedOut, asyncio.TimeoutError) as e:
            logger.error(f"Network error: {e}. Restarting in 10 seconds...")
            time.sleep(10)
        except Exception as e:
            logger.critical(f"Unhandled exception: {e}", exc_info=True)
            break

if __name__ == "__main__":
    main()
