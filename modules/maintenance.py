from modules.redis_client import redis_manager

class MaintenanceMode:
    """Режим обслуживания"""
    
    @staticmethod
    async def is_enabled() -> bool:
        """Проверка включён ли режим"""
        if not redis_manager.enabled:
            return False
        return await redis_manager.get("maintenance:enabled") == "1"
    
    @staticmethod
    async def get_reason() -> str:
        """Получить причину"""
        if not redis_manager.enabled:
            return ""
        return await redis_manager.get("maintenance:reason") or "Технические работы"
    
    @staticmethod
    async def enable(reason: str = "Технические работы", duration: int = 3600):
        """Включить режим обслуживания"""
        if not redis_manager.enabled:
            return
        await redis_manager.client.setex("maintenance:enabled", duration, "1")
        await redis_manager.client.setex("maintenance:reason", duration, reason)
    
    @staticmethod
    async def disable():
        """Выключить режим обслуживания"""
        if not redis_manager.enabled:
            return
        await redis_manager.delete("maintenance:enabled")
        await redis_manager.delete("maintenance:reason")

maintenance = MaintenanceMode()
