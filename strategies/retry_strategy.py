import asyncio
import logging
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

class RetryStrategy:
    def __init__(self, max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
        self.max_attempts = max_attempts
        self.delay = delay
        self.backoff = backoff
    
    async def execute(self, func: Callable, *args, **kwargs) -> Optional[Any]:
        current_delay = self.delay
        for attempt in range(self.max_attempts):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_attempts - 1:
                    raise
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {current_delay}s")
                await asyncio.sleep(current_delay)
                current_delay *= self.backoff
        return None
