from fastapi import Request, HTTPException
from modules.redis_client import redis_manager

async def rate_limit_middleware(request: Request, call_next):
    """Middleware для rate limiting API"""
    client_ip = request.client.host
    path = request.url.path
    
    # Исключаем health check
    if path in ["/health", "/metrics"]:
        return await call_next(request)
    
    key = f"api_rl:{client_ip}"
    
    if redis_manager.enabled:
        current = await redis_manager.incr(key)
        if current == 1:
            await redis_manager.client.expire(key, 60)
        
        if current > 100:  # 100 запросов в минуту
            raise HTTPException(status_code=429, detail="Too many requests")
    
    return await call_next(request)
