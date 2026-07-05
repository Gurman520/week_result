from telegram import Update
from telegram.ext import ContextTypes, filters, MessageHandler
from handlers.common import get_period_start
from handlers.start import apply_timezone_change
from database import get_user, save_entry
from pytz import timezone as pytz_timezone
import logging

logger = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Обработка ручного ввода часового пояса
    if context.user_data.get('awaiting_timezone'):
        tz_value = update.message.text.strip()
        context.user_data.pop('awaiting_timezone')
        try:
            pytz_timezone(tz_value)
        except:
            await update.message.reply_text("Неверный часовой пояс. Попробуй ещё раз или выбери из списка.")
            return
        await apply_timezone_change(user_id, tz_value, context)
        return

    if context.user_data.get('awaiting_timezone_change'):
        tz_value = update.message.text.strip()
        context.user_data.pop('awaiting_timezone_change')
        try:
            pytz_timezone(tz_value)
        except:
            await update.message.reply_text("Неверный часовой пояс. Попробуй ещё раз или выбери из списка.")
            return
        await apply_timezone_change(user_id, tz_value, context)
        return

    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    text = update.message.text
    freq = user[1]
    period_start = get_period_start(freq)
    await save_entry(user_id, text, period_start)
    logger.info(f"Entry saved for user {user_id}, period {period_start}")
    await update.message.reply_text("Записал! Спасибо, что поделился успехами.")

message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
