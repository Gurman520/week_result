import os
from dotenv import load_dotenv


load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    DB_NAME = os.getenv('DB_NAME', 'tracker.db')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    ADMIN_IDS = [int(id_) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_]
    MODEL_NAME = os.getenv('MODEL_NAME', 'qwen2.5:0.5b-instruct-q4_K_M')
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
