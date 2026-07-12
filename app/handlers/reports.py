from telegram import Update
from telegram.ext import ContextTypes
from handlers.common import ADMIN_IDS
from database import get_entries_for_month, save_report, get_user_reports, get_report_by_id
from llm import generate_summary
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Эта команда доступна только администраторам.")
        return

    now = datetime.now()
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1

    entries = await get_entries_for_month(user_id, year, month)
    if not entries:
        await update.message.reply_text(f"За {month}/{year} записей нет.")
        return

    combined = "\n---\n".join(entries)
    try:
        report = await generate_summary(combined)
        await save_report(user_id, year, month, report)
        await update.message.reply_text(f"Саммари за {month}/{year}:\n\n{report}")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        await update.message.reply_text("Ошибка при генерации отчёта.")

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reports = await get_user_reports(user_id)
    if not reports:
        await update.message.reply_text("У вас пока нет сохранённых отчётов.")
        return
    text = "Ваши отчёты (последние сверху):\n"
    for rid, rtype, period_start in reports:
        if rtype == 'week':
            # Преобразуем period_start (понедельник) в человеческий вид: "Неделя 29.06 – 05.07"
            start_date = datetime.strptime(period_start, '%Y-%m-%d')
            end_date = start_date + timedelta(days=6)
            period_str = f"{start_date.strftime('%d.%m')} – {end_date.strftime('%d.%m')}"
        else:
            period_str = f"{period_start[:7]}"  # YYYY-MM
        text += f"ID {rid}: {rtype} ({period_str})\n"
    text += "\nДля просмотра введите /report <ID>"
    await update.message.reply_text(text)

async def view_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Использование: /report <ID>")
        return
    try:
        report_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return

    row = await get_report_by_id(report_id)
    if not row or row[0] != user_id:
        await update.message.reply_text("Отчёт с таким ID не найден или не принадлежит вам.")
        return

    rtype, period_start, content = row[1], row[2], row[3]
    if rtype == 'week':
        start_date = datetime.strptime(period_start, '%Y-%m-%d')
        end_date = start_date + timedelta(days=6)
        header = f"Неделя {start_date.strftime('%d.%m')} – {end_date.strftime('%d.%m')}"
    else:
        header = f"Месяц {period_start[:7]}"
    await update.message.reply_text(f"Отчёт за {header}:\n\n{content}")
