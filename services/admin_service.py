import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from modules.database import db
from modules.config import config

logger = logging.getLogger(__name__)

app = FastAPI(title="Admin Service")

class BanRequest(BaseModel):
    user_id: int
    reason: str = "Нарушение правил"

class TierRequest(BaseModel):
    user_id: int
    tier: str

@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin-service"}

@app.post("/admin/ban")
async def ban_user(request: BanRequest):
    await db.ban_user(request.user_id, request.reason)
    return {"status": "banned", "user_id": request.user_id}

@app.post("/admin/unban")
async def unban_user(user_id: int):
    await db.unban_user(user_id)
    return {"status": "unbanned", "user_id": user_id}

@app.post("/admin/set_tier")
async def set_tier(request: TierRequest):
    if request.tier not in config.TIER_LIMITS:
        raise HTTPException(status_code=400, detail="Invalid tier")
    await db.set_user_tier(request.user_id, request.tier)
    return {"status": "updated", "user_id": request.user_id}

@app.get("/admin/stats")
async def get_stats():
    total_users = await db.get_total_users()
    today_requests = await db.get_today_requests()
    return {
        "total_users": total_users,
        "requests_today": today_requests,
        "active_workers": 10
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
