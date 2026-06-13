import asyncio
import logging
from datetime import datetime, timedelta
from modules.database import db

logger = logging.getLogger(__name__)

async def cleanup_worker():
    """Фоновый воркер для очистки БД (запуск каждый день в 3:00)"""
    while True:
        now = datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        try:
            await db.cleanup_old_history()
            logger.info("Очистка БД выполнена")
        except Exception as e:
            logger.error(f"Ошибка очистки: {e}")

# Алиас для обратной совместимости
start_cleanup_worker = cleanup_worker

if __name__ == "__main__":
    asyncio.run(cleanup_worker())
