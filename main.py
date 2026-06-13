#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import sys
from modules.config import config
from modules.database import db
from modules.redis_client import redis_manager
from services.api_gateway import start_api_gateway
from services.message_router import message_router
from workers.cleanup_worker import cleanup_worker

__version__ = "3.0.0"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    try:
        config.validate()
        
        logger.info(f"Запуск LLM Hub Bot v{__version__}")
        logger.info(f"Окружение: {config.ENVIRONMENT}")
        
        # Инициализация БД (без аргументов, так как init() читает DATABASE_URL из config)
        await db.init()
        
        # Инициализация Redis
        await redis_manager.init(config.REDIS_URL)
        
        # Запуск очереди сообщений
        await message_router.start()
        
        # Запуск фоновых задач
        asyncio.create_task(cleanup_worker())
        
        # Запуск API Gateway
        await start_api_gateway()
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
