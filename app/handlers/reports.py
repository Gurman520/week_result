from telegram import Update
from telegram.ext import ContextTypes
from handlers.common import ADMIN_IDS
from database import get_entries_for_month, save_report
from llm import generate_summary
import aiosqlite
from config import Config
from datetime import datetime
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
    async with aiosqlite.connect(Config.DB_NAME) as db:
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
    text += "\nЧтобы посмотреть отчёт, напишите /report ГГГГ ММ (например /report 2026 5)"
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

    async with aiosqlite.connect(Config.DB_NAME) as db:
        cursor = await db.execute(
            "SELECT content FROM reports WHERE user_id=? AND year=? AND month=?",
            (user_id, year, month)
        )
        row = await cursor.fetchone()
    if not row:
        await update.message.reply_text(f"Отчёт за {year}-{month:02d} не найден.")
        return
    await update.message.reply_text(f"Отчёт за {year}-{month:02d}:\n\n{row[0]}")
