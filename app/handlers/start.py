from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from pytz import timezone as pytz_timezone
from handlers.common import cancel, get_user_tz
from database import get_user, upsert_user, set_user_timezone as db_set_tz, set_vacation
from scheduler import schedule_reminder, get_user_tz
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        keyboard = [
            [InlineKeyboardButton("Москва (UTC+3)", callback_data="tz=Europe/Moscow")],
            [InlineKeyboardButton("Екатеринбург (UTC+5)", callback_data="tz=Asia/Yekaterinburg")],
            [InlineKeyboardButton("Новосибирск (UTC+7)", callback_data="tz=Asia/Novosibirsk")],
            [InlineKeyboardButton("UTC", callback_data="tz=UTC")],
            [InlineKeyboardButton("Другой (ввести вручную)", callback_data="tz=manual")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Привет! Я бот-трекер достижений. Выбери свой часовой пояс, чтобы напоминания приходили вовремя:",
            reply_markup=reply_markup
        )
        logger.info(f"New user {user_id} prompted for timezone")
    else:
        tz = get_user_tz(user)
        await update.message.reply_text(
            f"Ты уже зарегистрирован. Часовой пояс: {tz}.\n"
            "Используй /set_reminder для настройки напоминаний.\n"
            "/set_auto_report для автоматических отчётов.\n"
            "/reports для просмотра сохранённых отчётов.\n"
            "/set_timezone для смены часового пояса.\n"
            "/vacation N – уйти в отпуск на N дней (0 – отмена)."
        )

async def set_timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("tz="):
        tz_value = data.split("=", 1)[1]
        if tz_value == "manual":
            await query.edit_message_text(
                "Введи часовой пояс в формате IANA (например, Europe/London, Asia/Tokyo). "
                "Список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
            )
            context.user_data['awaiting_timezone'] = True
            return
        await db_set_tz(user_id, tz_value)
        await upsert_user(user_id, frequency='week', day_of_week=4, day_of_month=1,
                          time_hour=18, time_minute=0, timezone=tz_value, custom_days='', active=1)
        user_new = await get_user(user_id)
        schedule_reminder(context.application, user_new)
        await query.edit_message_text(
            f"Часовой пояс {tz_value} сохранён. Напоминание установлено на пятницу 18:00 (твоё местное).\n"
            "Используй /set_reminder для изменения."
        )
        logger.info(f"User {user_id} set timezone {tz_value}")

async def change_timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return
    keyboard = [
        [InlineKeyboardButton("Москва (UTC+3)", callback_data="tz_change=Europe/Moscow")],
        [InlineKeyboardButton("Екатеринбург (UTC+5)", callback_data="tz_change=Asia/Yekaterinburg")],
        [InlineKeyboardButton("Новосибирск (UTC+7)", callback_data="tz_change=Asia/Novosibirsk")],
        [InlineKeyboardButton("UTC", callback_data="tz_change=UTC")],
        [InlineKeyboardButton("Другой (ввести вручную)", callback_data="tz_change=manual")],
    ]
    await update.message.reply_text(
        "Текущий часовой пояс: " + get_user_tz(user) + "\nВыбери новый:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def change_timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("tz_change="):
        tz_value = data.split("=", 1)[1]
        if tz_value == "manual":
            await query.edit_message_text("Введи часовой пояс вручную:")
            context.user_data['awaiting_timezone_change'] = True
            return
        await apply_timezone_change(user_id, tz_value, context, query)

async def apply_timezone_change(user_id, tz_value, context, query=None):
    try:
        pytz_timezone(tz_value)
    except:
        msg = "Неверный часовой пояс."
        if query:
            await query.edit_message_text(msg)
        else:
            await context.bot.send_message(chat_id=user_id, text=msg)
        return
    await db_set_tz(user_id, tz_value)
    user = await get_user(user_id)
    schedule_reminder(context.application, user)
    msg = f"Часовой пояс изменён на {tz_value}."
    if query:
        await query.edit_message_text(msg)
    else:
        await context.bot.send_message(chat_id=user_id, text=msg)

async def vacation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Укажи количество дней отпуска: /vacation 7\nДля отмены: /vacation 0")
        return
    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Число дней должно быть целым.")
        return
    if days < 0:
        await update.message.reply_text("Число дней не может быть отрицательным.")
        return
    await set_vacation(user_id, days)
    if days == 0:
        await update.message.reply_text("Режим отпуска отменён. Напоминания снова активны.")
    else:
        until = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')
        await update.message.reply_text(f"Ты в отпуске до {until}. Напоминания приходить не будут.")
