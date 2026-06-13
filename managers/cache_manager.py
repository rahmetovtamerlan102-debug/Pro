from typing import Optional, Any, Callable
import json
from modules.redis_client import redis_manager

class CacheManager:
    DEFAULT_TTL = 3600
    
    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        data = await redis_manager.get(key)
        if data:
            return json.loads(data)
        return None
    
    @classmethod
    async def set(cls, key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
        await redis_manager.set(key, json.dumps(value), ttl)
    
    @classmethod
    async def delete(cls, key: str) -> None:
        await redis_manager.delete(key)
    
    @classmethod
    async def exists(cls, key: str) -> bool:
        return await redis_manager.get(key) is not None
    
    @classmethod
    async def remember(cls, key: str, ttl: int = DEFAULT_TTL, fetcher: Callable = None) -> Any:
        cached = await cls.get(key)
        if cached:
            return cached
        
        if fetcher:
            value = await fetcher()
            await cls.set(key, value, ttl)
            return value
        return None
