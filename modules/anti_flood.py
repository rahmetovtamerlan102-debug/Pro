from modules.redis_client import redis_manager

async def check_flood(ip: str, user_agent: str = "") -> tuple[bool, str]:
    """Проверка на флуд по IP"""
    if not redis_manager.enabled:
        return True, ""
    
    # Проверка заблокирован ли IP
    block_key = f"blocked_ip:{ip}"
    if await redis_manager.get(block_key):
        return False, "Ваш IP заблокирован за флуд"
    
    # Считаем запросы
    key = f"flood:{ip}"
    if user_agent:
        key = f"flood:{ip}:{user_agent[:50]}"
    
    count = await redis_manager.incr(key)
    if count == 1:
        await redis_manager.client.expire(key, 60)
    
    # 50 запросов в минуту -> блокировка
    if count > 50:
        await redis_manager.client.setex(block_key, 3600, "1")
        return False, "IP заблокирован на 1 час (слишком много запросов)"
    
    # 30 запросов в минуту -> предупреждение
    if count > 30:
        return True, "⚠️ Слишком много запросов! Замедлитесь."
    
    return True, ""

async def reset_flood(ip: str):
    """Сброс флуд-статистики для IP"""
    key = f"flood:{ip}"
    await redis_manager.delete(key)
