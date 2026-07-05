import asyncio
import logging
from datetime import datetime, time, timedelta, date
from typing import Optional
from dotenv import load_dotenv
from os import getenv
import ollama
import asyncio
from pytz import timezone as pytz_timezone
import logging.handlers


import aiosqlite
from telegram.error import NetworkError, TimedOut
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
# logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
# logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter(LOG_FORMAT)

# Консольный вывод
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Файловый вывод с ротацией (10 МБ, 3 бэкапа)
file_handler = logging.handlers.RotatingFileHandler(
    "bot.log", maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Применяем тот же уровень ко всем логгерам PTB и apscheduler
logging.getLogger('telegram').setLevel(logging.INFO)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

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
                timezone TEXT DEFAULT 'UTC',
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
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, year, month)
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
    
async def save_report(user_id: int, year: int, month: int, content: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reports (user_id, year, month, content) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, year, month) DO UPDATE SET content=excluded.content",
            (user_id, year, month, content)
        )
        await db.commit()

async def set_user_timezone(user_id: int, tz_str: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz_str, user_id))
        await db.commit()

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
    user_id = user_data[0]
    freq = user_data[1]
    day_of_week = user_data[2]
    day_of_month = user_data[3]
    t_hour = user_data[4]
    t_minute = user_data[5]
    tz_str = get_user_tz(user_data)

    job_id = build_job_id(user_id)
    if job_id in user_jobs:
        user_jobs[job_id].schedule_removal()
        del user_jobs[job_id]

    tz = pytz_timezone(tz_str) if tz_str != 'UTC' else None
    # Создаём aware time, если часовой пояс указан
    if tz:
        when_time = time(hour=t_hour, minute=t_minute, tzinfo=tz)
    else:
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
        async def monthly_check(context):
            now = datetime.now(tz=tz) if tz else datetime.now()
            if now.day == day_of_month:
                await remind_user(context)
        job = application.job_queue.run_daily(
            monthly_check, time=when_time, data=job_data
        )
    else:
        return

    user_jobs[job_id] = job
    logger.info(f"Scheduled reminder for user {user_id}: {freq} at {t_hour:02d}:{t_minute:02d} ({tz_str})")

async def restore_reminders(application: Application):
    """Восстанавливает задания для всех активных пользователей при старте."""
    users = await get_all_active_users()
    for user in users:
        schedule_reminder(application, user)

# ------------------- Обработчики команд -------------------
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
        if query:
            await query.edit_message_text("Неверный часовой пояс.")
        else:
            await context.bot.send_message(chat_id=user_id, text="Неверный часовой пояс.")
        return
    await set_user_timezone(user_id, tz_value)
    user = await get_user(user_id)
    schedule_reminder(context.application, user)
    msg = f"Часовой пояс изменён на {tz_value}."
    if query:
        await query.edit_message_text(msg)
    else:
        await context.bot.send_message(chat_id=user_id, text=msg)

def get_user_tz(user_data: tuple) -> str:
    """Извлекает строку часового пояса из записи пользователя (индекс 6)."""
    if len(user_data) > 6 and user_data[6]:
        return user_data[6]
    return 'UTC'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    user = await get_user(user_id)
    if not user:
        # Предлагаем выбрать часовой пояс
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
        logger.info(f"New user {user_id} (@{username}) prompted for timezone")
    else:
        tz_str = get_user_tz(user)
        await update.message.reply_text(
            f"Ты уже зарегистрирован. Часовой пояс: {tz_str}.\n"
            "Используй /set_reminder, чтобы изменить расписание.\n"
            "Используй /set_timezone, чтобы изменить часовой пояс."
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
                "Можно посмотреть список здесь: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
            )
            context.user_data['awaiting_timezone'] = True
            return
        # Сохраняем выбранный пояс
        await set_user_timezone(user_id, tz_value)
        # Создаём пользователя с настройками по умолчанию (пятница 18:00) и этим поясом
        await upsert_user(user_id, frequency='week', day_of_week=4, day_of_month=1,
                          time_hour=18, time_minute=0, timezone=tz_value, active=1)
        # Планируем напоминание
        user_new = await get_user(user_id)
        schedule_reminder(context.application, user_new)
        await query.edit_message_text(
            f"Часовой пояс {tz_value} сохранён. Напоминание установлено на пятницу 18:00 (твоё местное).\n"
            "Используй /set_reminder для изменения."
        )
        logger.info(f"User {user_id} set timezone {tz_value}")

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

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} requested report list")
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT year, month FROM reports WHERE user_id=? ORDER BY year DESC, month DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("У вас пока нет сохранённых отчётов.")
        return
    text = "Доступные отчёты:\n"
    for year, month in rows:
        text += f"- {year}-{month:02d}\n"
    text += "\nЧтобы посмотреть отчёт, напишите /report YYYY MM (например /report 2026 5)"
    await update.message.reply_text(text)

async def view_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Использование: /report ГГГГ ММ (например /report 2026 5)")
        return
    try:
        year = int(context.args[0])
        month = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Год и месяц должны быть числами.")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT content FROM reports WHERE user_id=? AND year=? AND month=?",
            (user_id, year, month)
        )
        row = await cursor.fetchone()
    if not row:
        await update.message.reply_text(f"Отчёт за {year}-{month:02d} не найден.")
        return
    await update.message.reply_text(f"Отчёт за {year}-{month:02d}:\n\n{row[0]}")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} requested help")
    
    text = "Доступные команды:\n" \
    "1. /set_reminder - изменить расписание\n" \
    "2.  /set_timezone - изменить часовой пояс\n" \
    "3. /summary - получить отчет за прошлый месяц (Позволяет перегенерировать отчет)\n" \
    "4. /reports - получить список доступных отчетов\n" \
    "5. /report - получить конкретный отчет из списка"
    await update.message.reply_text(text)

# ------------------- Настройка напоминания (ConversationHandler) -------------------
FREQ, DAY_WEEK, DAY_MONTH, TIME_INPUT = range(4)

async def set_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Каждый день", callback_data="day"),
         InlineKeyboardButton("Раз в неделю", callback_data="week")] #,
         # InlineKeyboardButton("Раз в месяц", callback_data="month")]
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
    # Проверяем, ожидается ли ввод часового пояса
    if context.user_data.get('awaiting_timezone'):
        tz_value = update.message.text.strip()
        try:
            pytz_timezone(tz_value)   # проверка валидности
        except:
            await update.message.reply_text("Неверный часовой пояс. Попробуй ещё раз или выбери из списка.")
            return
        context.user_data.pop('awaiting_timezone')
        await set_user_timezone(user_id, tz_value)
        await upsert_user(user_id, timezone=tz_value)
        user_new = await get_user(user_id)
        if user_new and user_new[6] == tz_value:
            schedule_reminder(context.application, user_new)
            await update.message.reply_text(f"Часовой пояс {tz_value} установлен.")
        return
    if context.user_data.get('awaiting_timezone_change'):
        tz_value = update.message.text.strip()
        context.user_data.pop('awaiting_timezone_change')
        await apply_timezone_change(user_id, tz_value, context)
        return
    
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

    entries = await get_entries_for_month(user_id, year, month)
    if not entries:
        logger.info(f"No entries for user {user_id} in {month}/{year}")
        await update.message.reply_text(f"За {month}/{year} записей нет.")
        return

    combined = "\n---\n".join(entries)
    prompt = (
        "Ты — персональный ассистент. На основе еженедельных заметок пользователя составь "
        "краткое структурированное резюме его рабочих достижений за месяц. "
        "Выдели ключевые проекты, решённые проблемы, полученные навыки. "
        "Пиши лаконично, без воды, в виде маркированного списка. "
        f"Заметки:\n{combined}"
    )

    try:
        response = await asyncio.to_thread(
            ollama.chat,
            model='qwen2.5:0.5b-instruct-q4_K_M',
            messages=[{'role': 'user', 'content': prompt}]
        )
        summary_text = response['message']['content']
        logger.info(f"LLM summary generated for user {user_id}")

        # Сохраняем отчёт в БД
        await save_report(user_id, year, month, summary_text)

        await update.message.reply_text(f"Саммари за {month}/{year}:\n\n{summary_text}")
    except Exception as e:
        logger.error(f"LLM call failed for user {user_id}: {e}")
        await update.message.reply_text(
            "Не удалось связаться с локальной моделью. Проверьте, что Ollama запущена и модель доступна."
        )

# ------------------- Запуск приложения -------------------
async def set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Регистрация и начало работы"),
        BotCommand("set_reminder", "Настроить расписание напоминаний"),
        BotCommand("summary", "Получить саммари за прошлый месяц"),
        BotCommand("reports", "Список сохранённых отчётов"),
        BotCommand("report", "Показать конкретный отчёт (пример: /report 2026 5)"),
        BotCommand("time", "Показать текущее время системы и напоминания"),
        # BotCommand("jobs", "Список активных задач напоминаний (отладка)"),
        BotCommand("set_timezone", "Изменить часовой пояс"),
    ]
    await application.bot.set_my_commands(commands)

async def post_init_actions(application: Application):
    await restore_reminders(application)
    await set_bot_commands(application)

def main():
    asyncio.run(init_db())

    # app = Application.builder().token(BOT_TOKEN).post_init(post_init_actions).build()
    app = Application.builder() \
        .token(BOT_TOKEN) \
        .connect_timeout(30) \
        .read_timeout(30) \
        .write_timeout(30) \
        .pool_timeout(30) \
        .post_init(post_init_actions) \
        .build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("jobs", list_jobs)) 
    app.add_handler(CommandHandler("time", debug_time))
    app.add_handler(CommandHandler("reports", list_reports))
    app.add_handler(CommandHandler("report", view_report))
    app.add_handler(CallbackQueryHandler(set_timezone_callback, pattern="^tz="))
    app.add_handler(CallbackQueryHandler(change_timezone_callback, pattern="^tz_change="))
    app.add_handler(CommandHandler("set_timezone", change_timezone_command))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # app.run_polling(allowed_updates=Update.ALL_TYPES)
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
