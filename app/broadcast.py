import asyncio
import logging
import database
from telegram.ext import Application

logger = logging.getLogger(__name__)

async def send_broadcasts(application: Application):
    broadcasts = await database.get_pending_broadcasts()
    if not broadcasts:
        logger.info("No pending broadcasts")
        return

    active_users = await database.get_all_active_users()
    if not active_users:
        logger.info("No active users for broadcast")
        # Помечаем все ожидающие сообщения как отправленные, чтобы не висели
        for b_id, _ in broadcasts:
            await database.mark_broadcast_sent(b_id)
        return

    for b_id, text in broadcasts:
        logger.info(f"Starting broadcast {b_id}")
        success = 0
        fail = 0
        for user in active_users:
            try:
                await application.bot.send_message(chat_id=user.get('user_id'), text=text)
                success += 1
                await asyncio.sleep(0.05)  # задержка, чтобы не превысить лимиты Telegram
            except Exception as e:
                logger.error(f"Failed to send broadcast to {user.get('user_id')}: {e}")
                fail += 1
        logger.info(f"Broadcast {b_id} sent: {success} ok, {fail} failed")
        await database.mark_broadcast_sent(b_id)