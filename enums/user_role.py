from enum import Enum

class UserRole(Enum):
    FREE = "free"
    PRO = "pro"
    ULTRA = "ultra"
    ADMIN = "admin"
    OWNER = "owner"
    
    @classmethod
    def has_permission(cls, role: str, required: str) -> bool:
        levels = {"free": 0, "pro": 1, "ultra": 2, "admin": 3, "owner": 4}
        return levels.get(role, 0) >= levels.get(required, 0)
