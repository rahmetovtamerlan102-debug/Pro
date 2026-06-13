import re
from typing import Tuple, Optional

class InputValidator:
    @staticmethod
    def validate_message(text: str, max_length: int = 4000) -> Tuple[bool, Optional[str]]:
        if not text:
            return False, "Сообщение не может быть пустым"
        
        if len(text) > max_length:
            return False, f"Сообщение слишком длинное (макс {max_length} символов)"
        
        # Проверка на спам-символы
        if re.search(r'[<>{}]', text):
            return False, "Сообщение содержит запрещённые символы"
        
        return True, None
    
    @staticmethod
    def sanitize(text: str) -> str:
        # Удаляем опасные символы
        text = re.sub(r'[<>{}]', '', text)
        # Ограничиваем длину
        if len(text) > 4000:
            text = text[:4000]
        return text.strip()
