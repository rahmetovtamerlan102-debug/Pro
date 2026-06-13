import asyncio
import logging
from modules.database import db
from modules.config import config
from services.llm_router import llm_router

logger = logging.getLogger(__name__)

class MessageRouter:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=config.QUEUE_MAX_SIZE)
        self._running = False
        self._workers = []
    
    async def start(self, num_workers: int = config.QUEUE_WORKERS):
        self._running = True
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        logger.info(f"Message router started with {num_workers} workers")
    
    async def stop(self):
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("Message router stopped")
    
    async def _worker(self, worker_id: int):
        while self._running:
            try:
                future, user_id, model, messages = await self.queue.get()
                result = await llm_router.route(model, messages)
                future.set_result((result.get("response", ""), result.get("model", "error")))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                future.set_exception(e)
            finally:
                self.queue.task_done()
    
    async def add(self, user_id: int, model: str, messages: list):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put((future, user_id, model, messages))
        return await future

# Создаём экземпляр
message_router = MessageRouter()
