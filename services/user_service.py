from modules.database import db
from modules.config import config
import logging

logger = logging.getLogger(__name__)

class UserService:
    async def get_profile(self, user_id: int) -> dict:
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        model = await db.get_user_model(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        
        return {
            "user_id": user_id,
            "tier": tier,
            "used_today": used,
            "limit": limit,
            "remaining": remaining,
            "model": config.MODEL_NAMES[model],
            "invited": invited,
            "bonus": bonus
        }
    
    async def change_model(self, user_id: int, model: str) -> bool:
        if model not in config.MODEL_LIST:
            return False
        await db.update_user_model(user_id, model)
        return True
    
    async def get_referral_link(self, user_id: int) -> str:
        code = await db.get_or_create_referral_code(user_id)
        return f"https://t.me/{config.BOT_USERNAME}?start={code}"
