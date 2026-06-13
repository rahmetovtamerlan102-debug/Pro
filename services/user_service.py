from modules.database import db
from modules.config import config

class UserService:
    """Сервис для работы с пользователями"""
    
    async def get_profile(self, user_id: int) -> dict:
        """Получение профиля пользователя"""
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        model = await db.get_user_model(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        history_count = await db.get_history_count(user_id)
        
        return {
            "tier": tier,
            "tier_name": config.TIER_NAMES.get(tier, tier),
            "used_today": used,
            "limit": limit,
            "remaining": remaining,
            "model": config.MODEL_NAMES.get(model, model),
            "invited": invited,
            "bonus": bonus,
            "total_messages": history_count
        }
    
    async def change_model(self, user_id: int, model: str) -> bool:
        """Смена модели пользователя"""
        if model not in config.MODEL_LIST:
            return False
        await db.update_user_model(user_id, model)
        return True
    
    async def get_referral_link(self, user_id: int) -> str:
        """Получение реферальной ссылки"""
        code = await db.get_or_create_referral_code(user_id)
        return f"https://t.me/{config.BOT_USERNAME}?start={code}"
    
    async def get_stats(self, user_id: int) -> dict:
        """Получение статистики пользователя"""
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        history_count = await db.get_history_count(user_id)
        
        return {
            "tier": tier,
            "tier_name": config.TIER_NAMES.get(tier, tier),
            "used_today": used,
            "limit": limit,
            "remaining": remaining,
            "total_messages": history_count
        }
    
    async def clear_history(self, user_id: int) -> None:
        """Очистка истории пользователя"""
        await db.clear_history(user_id)
    
    async def get_referral_info(self, user_id: int) -> tuple:
        """Получение реферальной информации"""
        return await db.get_referral_info(user_id)

# Создаём экземпляр для импорта
user_service = UserService()
