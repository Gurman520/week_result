from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from handlers.common import cancel, get_user_tz
from database import get_user, get_auto_report_configs
from scheduler import schedule_auto_report
import aiosqlite
from config import Config
import logging


logger = logging.getLogger(__name__)

AUTO_FREQ, AUTO_DAY_WEEK, AUTO_DAY_MONTH, AUTO_TIME_INPUT = range(4)

async def set_auto_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Раз в неделю", callback_data="week"),
         InlineKeyboardButton("Раз в месяц", callback_data="month")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери периодичность автоотчёта:", reply_markup=reply_markup)
    return AUTO_FREQ

async def auto_freq_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    freq = query.data
    context.user_data['auto_freq'] = freq
    if freq == 'week':
        keyboard = [
            [InlineKeyboardButton("Пн", callback_data="0"), InlineKeyboardButton("Вт", callback_data="1"),
             InlineKeyboardButton("Ср", callback_data="2"), InlineKeyboardButton("Чт", callback_data="3"),
             InlineKeyboardButton("Пт", callback_data="4")],
            [InlineKeyboardButton("Сб", callback_data="5"), InlineKeyboardButton("Вс", callback_data="6")]
        ]
        await query.edit_message_text("В какой день недели присылать отчёт?", reply_markup=InlineKeyboardMarkup(keyboard))
        return AUTO_DAY_WEEK
    else:
        await query.edit_message_text("Введи число месяца (1-28):")
        return AUTO_DAY_MONTH

async def auto_day_week_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['auto_day_week'] = int(query.data)
    await query.edit_message_text("Введи время в формате ЧЧ:ММ (например, 09:00):")
    return AUTO_TIME_INPUT

async def auto_day_month_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        day = int(update.message.text)
        if 1 <= day <= 28:
            context.user_data['auto_day_month'] = day
            await update.message.reply_text("Введи время в формате ЧЧ:ММ (например, 09:00):")
            return AUTO_TIME_INPUT
        else:
            await update.message.reply_text("Число должно быть от 1 до 28.")
            return AUTO_DAY_MONTH
    except ValueError:
        await update.message.reply_text("Введи число.")
        return AUTO_DAY_MONTH

async def auto_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        hour, minute = map(int, text.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except:
        await update.message.reply_text("Неверный формат. Введи как ЧЧ:ММ:")
        return AUTO_TIME_INPUT

    user_id = update.effective_user.id
    freq = context.user_data['auto_freq']
    day_of_week = context.user_data.get('auto_day_week', 0)
    day_of_month = context.user_data.get('auto_day_month', 1)

    async with aiosqlite.connect(Config.DB_NAME) as db:
        await db.execute(
            """INSERT INTO auto_reports (user_id, enabled, frequency, day_of_week, day_of_month, time_hour, time_minute)
               VALUES (?, 1, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               enabled=1, frequency=excluded.frequency, day_of_week=excluded.day_of_week,
               day_of_month=excluded.day_of_month, time_hour=excluded.time_hour, time_minute=excluded.time_minute""",
            (user_id, freq, day_of_week, day_of_month, hour, minute)
        )
        await db.commit()

    user = await get_user(user_id)
    tz_str = get_user_tz(user)
    schedule_auto_report(context.application, user_id, freq, day_of_week, day_of_month, hour, minute, tz_str)

    freq_text = {'week': 'раз в неделю', 'month': 'раз в месяц'}
    await update.message.reply_text(f"Автоотчёт настроен: {freq_text[freq]} в {hour:02d}:{minute:02d}.")
    return ConversationHandler.END

auto_report_conv = ConversationHandler(
    entry_points=[CommandHandler('set_auto_report', set_auto_report_start)],
    states={
        AUTO_FREQ: [CallbackQueryHandler(auto_freq_chosen)],
        AUTO_DAY_WEEK: [CallbackQueryHandler(auto_day_week_chosen)],
        AUTO_DAY_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_day_month_input)],
        AUTO_TIME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_time_input)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)
