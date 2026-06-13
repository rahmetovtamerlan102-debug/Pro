import re
from typing import Tuple

# Стоп-слова (можно загружать из БД)
BAD_WORDS = [
    "спам", "реклама", "порно", "насилие",
    "наркотики", "экстремизм", "терроризм"
]

BAD_PATTERNS = [
    r"https?://\S+\.ru",  # Ссылки на .ru
    r"\+\d{11,}",          # Телефоны
]

async def filter_message(text: str) -> Tuple[bool, str]:
    """Фильтрация сообщений"""
    text_lower = text.lower()
    
    # Проверка по словам
    for word in BAD_WORDS:
        if word in text_lower:
            return True, f"Обнаружено запрещённое слово: {word}"
    
    # Проверка по паттернам
    for pattern in BAD_PATTERNS:
        if re.search(pattern, text):
            return True, "Обнаружен запрещённый паттерн"
    
    return False, ""

async def is_allowed(message: str) -> bool:
    """Проверка разрешено ли сообщение"""
    blocked, _ = await filter_message(message)
    return not blocked
