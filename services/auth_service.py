from jose import jwt
from datetime import datetime, timedelta
from modules.config import config
import logging

logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self):
        self.secret = config.JWT_SECRET
        self.algorithm = config.JWT_ALGORITHM
        self.expire_minutes = config.JWT_EXPIRE_MINUTES
    
    def create_token(self, user_id: int, role: str = "user") -> str:
        payload = {
            "user_id": user_id,
            "role": role,
            "exp": datetime.utcnow() + timedelta(minutes=self.expire_minutes)
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)
    
    def verify_token(self, token: str) -> dict:
        try:
            return jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except Exception as e:
            logger.error(f"Token verification failed: {e}")
            return None
    
    def get_user_role(self, user_id: int, tier: str) -> str:
        if user_id in config.ADMIN_IDS:
            return "admin"
        return tier
