from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from datetime import datetime
from database import get_user
from scheduler import user_jobs, build_job_id

async def debug_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now_utc = datetime.utcnow()
    now_local = datetime.now()
    user = await get_user(user_id)

    text = f"• UTC сейчас: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
    text += f"• Локальное время системы: {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"

    if user:
        print(user)
        freq = user['frequency']
        h = user['time_hour']
        m = user['time_minute']
        tz = user.get('timezone', 'UTC')
        text += f"• Настроенное время: {h:02d}:{m:02d} (частота: {freq}, пояс: {tz})\n"

        job_id = build_job_id(user_id)
        print(user_jobs)
        if job_id in user_jobs:
            job = user_jobs[job_id]
            text += f"• Следующий запуск: {job.next_t}\n"
        else:
            text += "• Задача для вас не найдена!\n"
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
