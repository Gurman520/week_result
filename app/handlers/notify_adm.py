import logging
from config import Config


logger = logging.getLogger(__name__)

async def notify_admins(bot, text: str):
    """Отправляет сообщение всем админам из Config.ADMIN_IDS."""
    for admin_id in Config.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")
