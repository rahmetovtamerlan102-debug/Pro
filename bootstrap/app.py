import logging
from modules.database import db
from modules.redis_client import redis_manager
from modules.config import config

logger = logging.getLogger(__name__)

class Application:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def initialize(self):
        logger.info("Initializing application...")
        await db.init(config.DATABASE_URL)
        await redis_manager.init(config.REDIS_URL)
        logger.info("Application initialized")
    
    async def shutdown(self):
        logger.info("Shutting down...")
        await db.close()
        await redis_manager.close()
        logger.info("Shutdown complete")

app = Application()
