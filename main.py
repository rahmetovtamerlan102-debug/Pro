#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Hub Bot - Enterprise Architecture
Version: 5.0.0
"""

import asyncio
import logging
import sys
from modules.config import config
from modules.database import db
from modules.redis_client import redis_manager
from services.api_gateway import start_api_gateway
from workers.cleanup_worker import start_cleanup_worker
from workers.analytics_worker import start_analytics_worker
from services.ai_worker import start_ai_worker_pool

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    """Main entry point"""
    try:
        config.validate()
        
        logger.info(f"Starting LLM Hub Bot v{__version__}")
        logger.info(f"Environment: {config.ENVIRONMENT}")
        logger.info(f"Service: {config.SERVICE_NAME}")
        
        # Initialize database
        await db.init(config.DATABASE_URL)
        
        # Initialize Redis
        await redis_manager.init(config.REDIS_URL)
        
        # Start AI worker pool
        asyncio.create_task(start_ai_worker_pool())
        
        # Start background workers
        asyncio.create_task(start_cleanup_worker())
        asyncio.create_task(start_analytics_worker())
        
        # Start API Gateway
        await start_api_gateway()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
