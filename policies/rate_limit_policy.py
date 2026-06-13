from modules.redis_client import redis_manager

class RateLimitPolicy:
    def __init__(self, limit: int = 100, period: int = 60):
        self.limit = limit
        self.period = period
    
    async def check(self, key: str) -> bool:
        if not redis_manager.enabled:
            return True
        
        current = await redis_manager.incr(key)
        if current == 1:
            await redis_manager.client.expire(key, self.period)
        
        return current <= self.limit
