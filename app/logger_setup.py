import logging
import logging.handlers
from config import Config

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(Config.LOG_LEVEL)
    formatter = logging.Formatter(Config.LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        "bot.log", maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.DEBUG)
