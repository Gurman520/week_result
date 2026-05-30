import asyncio
import logging
from datetime import datetime, time, timedelta, date
from typing import Optional
from pytz import timezone
from dotenv import load_dotenv
from os import getenv


import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Defaults,
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# ------------------- Настройки -------------------
load_dotenv()
BOT_TOKEN = getenv('BOT_TOKEN')
DB_NAME = getenv('DB_NAME')
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ------------------- Работа с БД -------------------
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
                active INTEGER DEFAULT 1
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,   -- дата начала периода (YYYY-MM-DD)
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, period_start)
            );
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def upsert_user(user_id: int, **kwargs):
    columns = ', '.join(kwargs.keys())
    placeholders = ', '.join(['?'] * len(kwargs))
    updates = ', '.join(f"{k} = excluded.{k}" for k in kwargs)
    values = list(kwargs.values())

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            f"INSERT INTO users (user_id, {columns}) "
            f"VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}",
            [user_id] + values
        )
        await db.commit()

def get_period_start(freq: str, now: datetime = None) -> str:
    """Возвращает дату начала текущего периода (строка YYYY-MM-DD) в зависимости от частоты."""
    if now is None:
        now = datetime.now()
    if freq == 'day':
        return now.strftime('%Y-%m-%d')
    elif freq == 'week':
        # Понедельник текущей недели
        monday = now - timedelta(days=now.weekday())
        return monday.strftime('%Y-%m-%d')
    elif freq == 'month':
        return now.strftime('%Y-%m-01')
    return now.strftime('%Y-%m-%d')  # fallback

async def save_entry(user_id: int, content: str, period_start: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO entries (user_id, period_start, content) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, period_start) DO UPDATE SET content=excluded.content",
            (user_id, period_start, content)
        )
        await db.commit()

async def get_entries_for_month(user_id: int, year: int, month: int):
    """Получить все записи за месяц (для month) или за недели, попадающие в месяц."""
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

async def get_all_active_users():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE active = 1")
        return await cursor.fetchall()

# ------------------- Планировщик напоминаний -------------------
user_jobs = {}   # {job_id: Job}

async def remind_user(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет вопрос пользователю и планирует проверку через час."""
    job = context.job
    user_id = job.data['user_id']
    freq = job.data.get('freq', 'week')
    await context.bot.send_message(
        chat_id=user_id,
        text="Привет! Расскажи в 2-3 предложениях, что ты сделал за этот период."
    )
    # Планируем проверку через 1 час
    context.job_queue.run_once(
        check_response,
        when=3600,
        data={'user_id': user_id, 'freq': freq}
    )

async def check_response(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет, появилась ли запись за текущий период."""
    user_id = context.job.data['user_id']
    freq = context.job.data.get('freq', 'week')
    period_start = get_period_start(freq)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT 1 FROM entries WHERE user_id=? AND period_start=?",
            (user_id, period_start)
        )
        if not await cursor.fetchone():
            await context.bot.send_message(
                chat_id=user_id,
                text="Ты ещё не записал достижения. Удели минуту, это важно 🙂"
            )

def build_job_id(user_id: int) -> str:
    return f"remind_{user_id}"

def schedule_reminder(application: Application, user_data: tuple):
    """Создаёт или обновляет задание напоминания для пользователя."""
    user_id = user_data[0]
    freq = user_data[1]      # day / week / month
    day_of_week = user_data[2]
    day_of_month = user_data[3]
    t_hour = user_data[4]
    t_minute = user_data[5]

    job_id = build_job_id(user_id)
    # Удаляем старое задание, если есть
    if job_id in user_jobs:
        user_jobs[job_id].schedule_removal()
        del user_jobs[job_id]

    when_time = time(hour=t_hour, minute=t_minute)

    job_data = {'user_id': user_id, 'freq': freq}
    if freq == 'day':
        job = application.job_queue.run_daily(
            remind_user, time=when_time, data=job_data
        )
    elif freq == 'week':
        job = application.job_queue.run_daily(
            remind_user, time=when_time, days=(day_of_week,), data=job_data
        )
    elif freq == 'month':
        # Задание, срабатывающее каждый день в заданное время, но отправляющее напоминание
        # только если сегодня день месяца == day_of_month.
        async def monthly_check(context):
            now = datetime.now()
            if now.day == day_of_month:
                await remind_user(context)
        job = application.job_queue.run_daily(
            monthly_check, time=when_time, data=job_data
        )
    else:
        return

    user_jobs[job_id] = job

async def restore_reminders(application: Application):
    """Восстанавливает задания для всех активных пользователей при старте."""
    users = await get_all_active_users()
    for user in users:
        schedule_reminder(application, user)

# ------------------- Обработчики команд -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        # Устанавливаем стандартные настройки
        await upsert_user(user_id, frequency='week', day_of_week=4, day_of_month=1,
                          time_hour=18, time_minute=0, active=1)
        # Планируем напоминание
        schedule_reminder(context.application, (user_id, 'week', 4, 1, 18, 0))
        await update.message.reply_text(
            "Привет! Я бот-трекер достижений. Я буду напоминать тебе записывать успехи.\n"
            "По умолчанию напоминание — каждую пятницу в 18:00.\n"
            "Используй /set_reminder, чтобы изменить расписание."
        )
    else:
        await update.message.reply_text(
            "Ты уже зарегистрирован. Чтобы изменить настройки, используй /set_reminder.\n"
            "Чтобы получить сводку за месяц, напиши /summary."
        )

async def list_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"User {user_id} requested /jobs")  # Логирование от кого debug запрос

    jobs = context.application.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("Нет активных задач.")
        return
    text = "Активные задачи:\n"
    for job in jobs:
        text += f"- {job.name}: next={job.next_t}\n"
    await update.message.reply_text(text)

async def debug_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"User {user_id} requested /time")  # Логирование от кого debug запрос

    now_utc = datetime.utcnow()
    now_local = datetime.now()  # локальное время вашего ПК/сервера
    user = await get_user(user_id)

    text = f"• UTC сейчас: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
    text += f"• Локальное время системы: {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"

    if user:
        freq = user[1]
        h, m = user[4], user[5]
        text += f"• Ваше настроенное время: {h:02d}:{m:02d} (частота: {freq})\n"
        # Покажем информацию о задачах
        job_id = build_job_id(user_id)
        if job_id in user_jobs:
            job = user_jobs[job_id]
            text += f"• Следующий запуск задачи: {job.next_t}\n"
        else:
            text += "• Задача для вас не найдена в планировщике!\n"
    else:
        text += "• Вы не зарегистрированы. Нажмите /start"

    await update.message.reply_text(text)

# ------------------- Настройка напоминания (ConversationHandler) -------------------
FREQ, DAY_WEEK, DAY_MONTH, TIME_INPUT = range(4)

async def set_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Каждый день", callback_data="day"),
         InlineKeyboardButton("Раз в неделю", callback_data="week"),
         InlineKeyboardButton("Раз в месяц", callback_data="month")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери частоту напоминаний:", reply_markup=reply_markup)
    return FREQ

async def freq_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    freq = query.data
    context.user_data['freq'] = freq
    if freq == 'day':
        await query.edit_message_text("Введи время в формате ЧЧ:ММ (например, 18:00):")
        return TIME_INPUT
    elif freq == 'week':
        keyboard = [
            [InlineKeyboardButton("Пн", callback_data="0"), InlineKeyboardButton("Вт", callback_data="1"),
             InlineKeyboardButton("Ср", callback_data="2"), InlineKeyboardButton("Чт", callback_data="3"),
             InlineKeyboardButton("Пт", callback_data="4")],
            [InlineKeyboardButton("Сб", callback_data="5"), InlineKeyboardButton("Вс", callback_data="6")]
        ]
        await query.edit_message_text("Выбери день недели:", reply_markup=InlineKeyboardMarkup(keyboard))
        return DAY_WEEK
    elif freq == 'month':
        await query.edit_message_text("Введи число месяца (1-28):")
        return DAY_MONTH

async def day_week_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['day_week'] = int(query.data)
    await query.edit_message_text("Введи время в формате ЧЧ:ММ (например, 18:00):")
    return TIME_INPUT

async def day_month_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        day = int(update.message.text)
        if 1 <= day <= 28:
            context.user_data['day_month'] = day
            await update.message.reply_text("Введи время в формате ЧЧ:ММ (например, 18:00):")
            return TIME_INPUT
        else:
            await update.message.reply_text("Число должно быть от 1 до 28. Попробуй ещё раз:")
            return DAY_MONTH
    except ValueError:
        await update.message.reply_text("Введи число. Например: 15")
        return DAY_MONTH

async def time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        hour, minute = map(int, text.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except:
        await update.message.reply_text("Неверный формат. Введи время как ЧЧ:ММ, например 09:30:")
        return TIME_INPUT

    user_id = update.effective_user.id
    freq = context.user_data['freq']
    day_of_week = context.user_data.get('day_week', 4)
    day_of_month = context.user_data.get('day_month', 1)

    await upsert_user(user_id, frequency=freq, day_of_week=day_of_week,
                      day_of_month=day_of_month, time_hour=hour, time_minute=minute)
    # Получаем свежие данные и обновляем напоминание
    user_new = await get_user(user_id)
    schedule_reminder(context.application, user_new)

    freq_text = {'day': 'каждый день', 'week': 'раз в неделю', 'month': 'раз в месяц'}
    await update.message.reply_text(
        f"Настройки сохранены! Буду напоминать {freq_text[freq]} в {hour:02d}:{minute:02d}."
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Настройка отменена.")
    return ConversationHandler.END

conv_handler = ConversationHandler(
    entry_points=[CommandHandler('set_reminder', set_reminder_start)],
    states={
        FREQ: [CallbackQueryHandler(freq_chosen, pattern=None)],  # pattern=None оставляем
        DAY_WEEK: [CallbackQueryHandler(day_week_chosen, pattern=None)],
        DAY_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, day_month_input)],
        TIME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, time_input)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)

# ------------------- Сохранение сообщений -------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любой текст сохраняем как запись за текущий период (в зависимости от частоты)."""
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    text = update.message.text
    freq = user[1]
    period_start = get_period_start(freq)
    await save_entry(user_id, text, period_start)
    await update.message.reply_text("Записал! Спасибо, что поделился успехами.")

# ------------------- Саммари (заглушка LLM) -------------------
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} requested summary")
    
    now = datetime.now()
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1

    print(year, month)
    entries = await get_entries_for_month(user_id, year, month)
    if not entries:
        await update.message.reply_text(f"За {month}/{year} записей нет.")
        return

    combined = "\n---\n".join(entries)
    # Здесь вызов LLM — пока заглушка
    llm_response = f"🤖 Саммари за {month}/{year} (заглушка):\n\n{combined[:1000]}..."
    
    await update.message.reply_text(llm_response)

# ------------------- Запуск приложения -------------------

def main():
    asyncio.run(init_db())

    app = Application.builder().token(BOT_TOKEN).defaults(Defaults(tzinfo=timezone('Europe/Moscow'))).post_init(restore_reminders).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("jobs", list_jobs)) 
    app.add_handler(CommandHandler("time", debug_time))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
