from telegram import Update
from telegram.ext import ConversationHandler
from config import Config
from scheduler import build_job_id as _build_job_id, get_period_start as _get_period_start

ADMIN_IDS = Config.ADMIN_IDS

def build_job_id(user_id: int) -> str:
    return _build_job_id(user_id)

def get_period_start(freq: str, now=None) -> str:
    return _get_period_start(freq, now)

def get_user_tz(user: dict) -> str:
    return user.get('timezone', 'UTC')

async def cancel(update: Update, context):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END
