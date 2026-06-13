import hashlib
from typing import Optional
from modules.redis_client import redis_manager

class PromptCache:
    """Кэширование популярных запросов"""
    
    @staticmethod
    async def get(prompt: str, model: str) -> Optional[str]:
        """Получить закэшированный ответ"""
        if not redis_manager.enabled:
            return None
        
        key = PromptCache._make_key(prompt, model)
        return await redis_manager.get(key)
    
    @staticmethod
    async def set(prompt: str, model: str, response: str):
        """Сохранить в кэш"""
        if not redis_manager.enabled:
            return
        
        # Кэшируем только короткие запросы и ответы
        if len(prompt) > 100 or len(response) > 500:
            return
        
        key = PromptCache._make_key(prompt, model)
        await redis_manager.client.setex(key, 86400, response)  # 24 часа
    
    @staticmethod
    def _make_key(prompt: str, model: str) -> str:
        content = f"{model}:{prompt[:200]}"
        return f"cache:{hashlib.md5(content.encode()).hexdigest()}"

prompt_cache = PromptCache()
