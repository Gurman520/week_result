from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from datetime import datetime
from handlers.common import get_user_tz
from database import get_user
from scheduler import user_jobs, build_job_id

async def debug_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now_utc = datetime.utcnow()
    now_local = datetime.now()
    user = await get_user(user_id)
    text = ...
    if user:
        freq = user['frequency']
        h, m = user['time_hour'], user['time_minute']   # было user[4], user[5]
        tz = user.get('timezone', 'UTC')
        text += f"• Настроенное время: {h:02d}:{m:02d} (частота: {freq}, пояс: {tz})\n"
        job_id = build_job_id(user_id)
        if job_id in user_jobs:
            job = user_jobs[job_id]
            text += f"• Следующий запуск: {job.next_t}\n"
        else:
            text += "• Задача для вас не найдена в планировщике!\n"
    else:
        text += "• Вы не зарегистрированы."
    await update.message.reply_text(text)

async def list_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.application.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("Нет активных задач.")
        return
    text = "Активные задачи:\n"
    for job in jobs:
        text += f"- {job.name}: next={job.next_t}\n"
    await update.message.reply_text(text)

debug_time_handler = CommandHandler('time', debug_time)
debug_jobs_handler = CommandHandler('jobs', list_jobs)
