import redis.asyncio as redis
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class RedisManager:
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._enabled = False
    
    async def init(self, url: str) -> None:
        try:
            self.client = await redis.from_url(url, decode_responses=True)
            await self.client.ping()
            self._enabled = True
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}")
            self._enabled = False
    
    async def close(self) -> None:
        if self.client:
            await self.client.close()
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    async def get(self, key: str) -> Optional[str]:
        if not self._enabled:
            return None
        return await self.client.get(key)
    
    async def set(self, key: str, value: str, ttl: int = None) -> None:
        if not self._enabled:
            return
        if ttl:
            await self.client.setex(key, ttl, value)
        else:
            await self.client.set(key, value)
    
    async def delete(self, key: str) -> None:
        if self._enabled:
            await self.client.delete(key)
    
    async def incr(self, key: str, expire: int = None) -> int:
        if not self._enabled:
            return 1
        val = await self.client.incr(key)
        if expire and val == 1:
            await self.client.expire(key, expire)
        return val

redis_manager = RedisManager()
