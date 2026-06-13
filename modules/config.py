import os
from typing import List, Dict, Optional
from dataclasses import dataclass, field

@dataclass
class Config:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "LLMHubBot")
    ADMIN_IDS: List[int] = field(default_factory=lambda: [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()])
    
    # API Keys
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DATABASE_POOL_MIN: int = int(os.getenv("DATABASE_POOL_MIN", "2"))
    DATABASE_POOL_MAX: int = int(os.getenv("DATABASE_POOL_MAX", "10"))
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Security
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-key-change-me")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    # Server
    PORT: int = int(os.environ.get("PORT", 10000))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "api-gateway")
    
    # Limits
    MAX_MESSAGE_LEN: int = 8000
    MAX_HISTORY: int = 100
    REFERRAL_BONUS: int = 20
    
    # Rate Limits
    RATE_LIMIT_REQUESTS: int = 2
    RATE_LIMIT_PERIOD: int = 1
    SPAM_THRESHOLD: int = 50
    SPAM_WINDOW: int = 60
    SPAM_WARN_THRESHOLD: int = 30
    
    # Queue
    QUEUE_MAX_SIZE: int = 200
    QUEUE_WORKERS: int = 10
    REQUEST_TIMEOUT: int = 40
    
    # Cache
    CACHE_TTL: int = 86400
    
    # Models
    MODEL_LIST: List[str] = field(default_factory=lambda: [
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ])
    
    MODEL_NAMES: Dict[str, str] = field(default_factory=lambda: {
        "meta-llama/llama-4-scout-17b-16e-instruct": "Llama 4 Scout",
        "llama-3.3-70b-versatile": "Llama 3.3 70B",
        "llama-3.1-8b-instant": "Llama 3.1 8B",
        "qwen/qwen3-32b": "Qwen 3 32B",
    })
    
    TIER_LIMITS: Dict[str, int] = field(default_factory=lambda: {"free": 30, "pro": 500, "ultra": 10000})
    TIER_NAMES: Dict[str, str] = field(default_factory=lambda: {"free": "Бесплатный", "pro": "PRO", "ultra": "ULTRA"})
    
    @classmethod
    def validate(cls) -> bool:
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN обязателен")
        if not cls.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY обязателен")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL обязателен")
        return True

config = Config()
