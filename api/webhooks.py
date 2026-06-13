from fastapi import APIRouter, Request, HTTPException
from modules.payments import verify_crypto_signature, process_payment_webhook
from modules.database import db

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@router.post("/crypto")
async def crypto_webhook(request: Request):
    """Webhook для CryptoBot"""
    signature = request.headers.get("crypto-pay-api-signature", "")
    body = await request.body()
    
    if not verify_crypto_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    data = await request.json()
    result = await process_payment_webhook(data)
    
    if result["status"] == "success":
        # Обновляем тариф пользователя
        await db.set_user_tier(result["user_id"], result["tier"])
        return {"ok": True}
    
    return {"ok": False, "error": result.get("message")}
