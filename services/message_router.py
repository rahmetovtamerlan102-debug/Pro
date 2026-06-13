import asyncio
import hashlib
from modules.database import db
from modules.redis_client import redis_manager
from modules.config import config
from services.llm_router import llm_router
from modules.spam_detector import spam_detector
import logging

logger = logging.getLogger(__name__)

class MessageRouter:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=config.QUEUE_MAX_SIZE)
        self._running = False
    
    async def process(self, user_id: int, text: str) -> None:
        # Check spam
        is_spam, warning = spam_detector.check(user_id)
        if is_spam:
            await db.ban_user(user_id, "Auto-ban: spam detected")
            return
        
        # Get history
        history = await db.get_history(user_id, 20)
        await db.add_history(user_id, "user", text)
        history.append({"role": "user", "content": text})
        
        # Check cache
        model = await db.get_user_model(user_id)
        cache_key = hashlib.md5(f"{model}:{text[:100]}".encode()).hexdigest()
        cached = await redis_manager.get(cache_key)
        if cached:
            return cached
        
        # Add to queue
        await self.queue.put((user_id, model, history))
    
    async def worker(self, worker_id: int):
        while self._running:
            try:
                user_id, model, history = await self.queue.get()
                result, used_model = await llm_router.route(model, history)
                await db.add_history(user_id, "assistant", result, used_model)
                
                # Cache short responses
                if len(result) < 500:
                    cache_key = hashlib.md5(f"{model}:{history[-1]['content'][:100]}".encode()).hexdigest()
                    await redis_manager.set(cache_key, result[:1000], config.CACHE_TTL)
                
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
            finally:
                self.queue.task_done()
    
    async def start(self, num_workers: int = config.QUEUE_WORKERS):
        self._running = True
        for i in range(num_workers):
            asyncio.create_task(self.worker(i))
        logger.info(f"Message router started with {num_workers} workers")
    
    async def stop(self):
        self._running = False
