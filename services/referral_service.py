from modules.database import db
from modules.config import config

async def process_referral(new_user_id: int, ref_code: str) -> dict:
    """Обработка реферальной ссылки"""
    # Находим пригласившего
    referrer = await db.get_user_by_ref_code(ref_code)
    
    if not referrer:
        return {"success": False, "message": "Неверный реферальный код"}
    
    if referrer['user_id'] == new_user_id:
        return {"success": False, "message": "Нельзя пригласить самого себя"}
    
    # Проверяем, не активировал ли уже пользователь рефералку
    existing = await db.get_user_referrer(new_user_id)
    if existing:
        return {"success": False, "message": "Вы уже активировали реферальный код"}
    
    # Начисляем бонусы
    await db.add_bonus_requests(new_user_id, config.REFERRAL_BONUS)
    await db.add_bonus_requests(referrer['user_id'], config.REFERRAL_BONUS)
    
    # Сохраняем связь
    await db.set_user_referrer(new_user_id, referrer['user_id'])
    
    # Увеличиваем счётчик приглашений
    await db.increment_invites(referrer['user_id'])
    
    return {
        "success": True,
        "message": f"Вы и ваш друг получили +{config.REFERRAL_BONUS} бонусных запросов!",
        "referrer_id": referrer['user_id']
    }

async def get_referral_info(user_id: int) -> dict:
    """Получение реферальной статистики"""
    invited_count = await db.get_invited_count(user_id)
    bonus_balance = await db.get_bonus_balance(user_id)
    referral_code = await db.get_or_create_referral_code(user_id)
    
    return {
        "code": referral_code,
        "invited_count": invited_count,
        "bonus_balance": bonus_balance,
        "link": f"https://t.me/{config.BOT_USERNAME}?start={referral_code}"
    }
