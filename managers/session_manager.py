import uuid
import json
import time
from typing import Optional, Dict, Any
from modules.redis_client import redis_manager

class SessionManager:
    SESSION_TTL = 86400  # 24 часа
    
    @classmethod
    async def create(cls, user_id: int, data: Dict[str, Any] = None) -> str:
        session_id = str(uuid.uuid4())
        session_data = {
            "user_id": user_id,
            "created_at": time.time(),
            "data": data or {}
        }
        await redis_manager.set(f"session:{session_id}", json.dumps(session_data), cls.SESSION_TTL)
        return session_id
    
    @classmethod
    async def get(cls, session_id: str) -> Optional[Dict[str, Any]]:
        data = await redis_manager.get(f"session:{session_id}")
        if data:
            return json.loads(data)
        return None
    
    @classmethod
    async def delete(cls, session_id: str) -> None:
        await redis_manager.delete(f"session:{session_id}")
    
    @classmethod
    async def get_user_id(cls, session_id: str) -> Optional[int]:
        session = await cls.get(session_id)
        return session.get("user_id") if session else None
