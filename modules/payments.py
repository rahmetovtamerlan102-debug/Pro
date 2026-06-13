import hmac
import hashlib
from modules.config import config

async def verify_crypto_signature(body: bytes, signature: str) -> bool:
    """Проверка подписи CryptoBot"""
    if not config.CRYPTOBOT_SECRET:
        return True
    
    expected = hmac.new(
        config.CRYPTOBOT_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature)

async def process_payment_webhook(data: dict) -> dict:
    """Обработка вебхука от CryptoBot"""
    if data.get("update_type") != "invoice_paid":
        return {"status": "ignored"}
    
    payload = data.get("payload", {})
    user_id = int(payload.get("payload", 0))
    amount = payload.get("paid_amount")
    
    if not user_id:
        return {"status": "error", "message": "No user_id"}
    
    if amount == "3.00":
        tier = "pro"
    elif amount == "10.00":
        tier = "ultra"
    else:
        return {"status": "error", "message": "Invalid amount"}
    
    return {
        "status": "success",
        "user_id": user_id,
        "tier": tier,
        "amount": amount
    }
