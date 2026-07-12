import llm
import logging
import database
import aiosqlite
from config import Config
from telegram.ext import ContextTypes
from pytz import timezone as pytz_timezone
from datetime import datetime, time, timedelta, date


logger = logging.getLogger(__name__)
user_jobs = {}

def ptb_weekday(now: datetime) -> int:
    """Возвращает день недели в формате (0=вс, 1=пн, ..., 6=сб)."""
    return now.isoweekday() % 7

def last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day

def get_user_tz(user: dict) -> str:
    return user.get('timezone', 'UTC')

def get_custom_days(user: dict) -> set:
    custom_days_str = user.get('custom_days', '')
    if custom_days_str:
        return set(map(int, custom_days_str.split(',')))
    return set(range(7))  # все дни

def is_on_vacation(user: dict, now: datetime) -> bool:
    vacation_until = user.get('vacation_until')
    if vacation_until:
        try:
            until = datetime.strptime(vacation_until, '%Y-%m-%d').date()
            return now.date() <= until
        except:
            pass
    return False

async def remind_user(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data['user_id']
    freq = job.data.get('freq', 'week')
    await context.bot.send_message(chat_id=user_id,
                                   text="Привет! Расскажи в 2-3 предложениях, что ты сделал за этот период.")
    context.job_queue.run_once(check_response, when=3600,
                               data={'user_id': user_id, 'freq': freq})

async def check_response(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data['user_id']
    freq = context.job.data.get('freq', 'week')
    period_start = get_period_start(freq)
    async with aiosqlite.connect(Config.DB_NAME) as db:
        cursor = await db.execute("SELECT 1 FROM entries WHERE user_id=? AND period_start=?",
                                  (user_id, period_start))
        if not await cursor.fetchone():
            await context.bot.send_message(chat_id=user_id,
                                           text="Ты ещё не записал достижения. Удели минуту, это важно 🙂")

def get_period_start(freq: str, now: datetime = None) -> str:
    if now is None:
        now = datetime.now()
    if freq == 'day':
        return now.strftime('%Y-%m-%d')
    elif freq == 'week':
        monday = now - timedelta(days=now.weekday())
        return monday.strftime('%Y-%m-%d')
    elif freq == 'month':
        return now.strftime('%Y-%m-01')
    return now.strftime('%Y-%m-%d')

def build_job_id(user_id: int, prefix: str = "remind") -> str:
    return f"{prefix}_{user_id}"

def schedule_reminder(application, user: dict):
    user_id = user['user_id']
    freq = user['frequency']
    day_of_week = user['day_of_week']
    day_of_month = user['day_of_month']
    t_hour = user['time_hour']
    t_minute = user['time_minute']
    tz_str = get_user_tz(user)

    job_id = build_job_id(user_id)
    if job_id in user_jobs:
        user_jobs[job_id].schedule_removal()
        del user_jobs[job_id]

    tz = pytz_timezone(tz_str) if tz_str and tz_str != 'UTC' else None
    when_time = time(hour=t_hour, minute=t_minute, tzinfo=tz) if tz else time(hour=t_hour, minute=t_minute)
    job_data = {'user_id': user_id, 'freq': freq}

    if freq == 'day':
        custom_days = get_custom_days(user)
        async def daily_check(context):
            now = datetime.now(tz=tz) if tz else datetime.now()
            if ptb_weekday(now) in custom_days and not is_on_vacation(user, now):
                await remind_user(context)
        job = application.job_queue.run_daily(daily_check, time=when_time, data=job_data, name=job_id)
    elif freq == 'month':
        async def monthly_check(context):
            now = datetime.now(tz=tz) if tz else datetime.now()
            last_day = last_day_of_month(now.year, now.month)
            target = min(day_of_month, last_day)
            if now.day == target and not is_on_vacation(user, now):
                await remind_user(context)
        job = application.job_queue.run_daily(monthly_check, time=when_time, data=job_data, name=job_id)
    elif freq == 'week':
        async def weekly_check(context):
            now = datetime.now(tz=tz) if tz else datetime.now()
            if ptb_weekday(now) == day_of_week and not is_on_vacation(user, now):
                await remind_user(context)
        job = application.job_queue.run_daily(weekly_check, time=when_time, days=(day_of_week,), data=job_data, name=job_id)
    else:
        return
    user_jobs[job_id] = job
    logger.info(f"Scheduled reminder for user {user_id}: {freq} at {t_hour:02d}:{t_minute:02d} ({tz_str})")

async def restore_reminders(application):
    users = await database.get_all_active_users()
    for user in users:
        schedule_reminder(application, user)
    logger.info(f"Restored {len(users)} reminder jobs")

def schedule_auto_report(application, user_id: int, freq: str, day_of_week: int,
                         day_of_month: int, hour: int, minute: int, tz_str: str):
    job_id = f"auto_report_{user_id}"
    for job in application.job_queue.jobs():
        if job.name == job_id:
            job.schedule_removal()
            break

    tz = pytz_timezone(tz_str) if tz_str and tz_str != 'UTC' else None
    when_time = time(hour=hour, minute=minute, tzinfo=tz) if tz else time(hour=hour, minute=minute)

    async def send_auto_report(context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"Auto report triggered for user {user_id}")
        now = datetime.now(tz=tz) if tz else datetime.now()
        logger.info(f"Current time: {now}, weekday={ptb_weekday(now)}, day={now.day}")
        user = await database.get_user(user_id)
        logger.info(f"Auto report GET user {user}")
        if is_on_vacation(user, now):
            return
        if freq == 'week':
            end = now - timedelta(days=now.weekday() + 1)   # прошлое воскресенье
            start = end - timedelta(days=6)                 # прошлый понедельник
            year = start.year
            month = start.month
            period_str = f"{start.strftime('%d.%m')} – {end.strftime('%d.%m')}"
            # вместо get_entries_for_month
            entries = await database.get_entries_for_dates(user_id, start.strftime('%Y-%m-%d'), (end + timedelta(days=1)).strftime('%Y-%m-%d'))
        else:  # month
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1
            period_str = f"{month:02d}.{year}"
            entries = await database.get_entries_for_month(user_id, year, month)
        if not entries:
            logger.info(f"No entries for auto report user {user_id} for {period_str}")
            await context.bot.send_message(chat_id=user_id, text=f"За {period_str} нет записей, отчёт не сформирован.")
            return

        combined = "\n---\n".join(entries)
        try:
            report_text = await llm.generate_summary(combined)
            await database.save_report(user_id, freq, start.strftime('%Y-%m-%d'), report_text)
            await context.bot.send_message(chat_id=user_id, text=f"Твой автоотчёт за {period_str}:\n\n{report_text}")
            logger.info(f"Auto report sent to user {user_id} for {period_str}")
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text=f"Кажется что-то сломалось при формировании твоего отчета.\nСообщение об ошибке уже отправлено разработчику")
            logger.error(f"Failed auto report for user {user_id}: {e}")

    if freq == 'week':
        application.job_queue.run_daily(send_auto_report, time=when_time, days=(day_of_week,), name=job_id)
    else:
        async def monthly_auto_report(context):
            now = datetime.now(tz=tz) if tz else datetime.now()
            last_day = last_day_of_month(now.year, now.month)
            target = min(day_of_month, last_day)
            if now.day == target:
                await send_auto_report(context)
        application.job_queue.run_daily(monthly_auto_report, time=when_time, name=job_id)

async def restore_auto_reports(application):
    configs = await database.get_auto_report_configs()
    for cfg in configs:
        user_id = cfg[0]
        enabled = cfg[1]
        if enabled:
            freq = cfg[2]
            day_of_week = cfg[3]
            day_of_month = cfg[4]
            hour = cfg[5]
            minute = cfg[6]
            user = await database.get_user(user_id)
            tz_str = get_user_tz(user) if user else 'UTC'
            schedule_auto_report(application, user_id, freq, day_of_week, day_of_month, hour, minute, tz_str)
    logger.info(f"Restored {len(configs)} auto report jobs")
