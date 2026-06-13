import asyncio
import logging
from datetime import datetime, timedelta
from modules.database import db

logger = logging.getLogger(__name__)

async def cleanup_worker():
    while True:
        now = datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        try:
            await db.cleanup_old_history()
            logger.info("Cleanup completed")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

if __name__ == "__main__":
    asyncio.run(cleanup_worker())
