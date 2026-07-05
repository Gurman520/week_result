from telegram import Update
from telegram.ext import ContextTypes
from handlers.common import ADMIN_IDS
from database import create_broadcast


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Недостаточно прав.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /broadcast <текст сообщения>")
        return

    text = ' '.join(context.args)
    await create_broadcast(text)
    await update.message.reply_text("Сообщение поставлено в очередь на рассылку при следующем запуске бота.")
