import asyncio
import ollama
from config import Config

def build_summary_prompt(notes: str) -> str:
    system_prompt = (
        "Ты — персональный ассистент, составляющий отчёт о рабочих достижениях. "
        "На основе предоставленных заметок составь структурированное резюме. "
        "Обязательно используй следующие разделы:\n"
        "- Основные проекты и задачи\n"
        "- Решённые проблемы и вызовы\n"
        "- Полученные навыки и знания\n"
        "Пиши на русском языке, кратко, каждую мысль с новой строки. "
        "Используй маркированный список (знаки дефиса). Не добавляй лишней информации."
    )
    return f"{system_prompt}\n\nЗаметки:\n{notes}"

async def generate_summary(notes: str) -> str:
    prompt = build_summary_prompt(notes)
    response = await asyncio.to_thread(
        ollama.chat,
        model=Config.MODEL_NAME,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response['message']['content']
