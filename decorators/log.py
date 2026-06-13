import logging
import time
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)

def log_execution(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        start = time.time()
        logger.info(f"Executing {func.__name__}...")
        try:
            result = await func(*args, **kwargs)
            elapsed = time.time() - start
            logger.info(f"{func.__name__} completed in {elapsed:.2f}s")
            return result
        except Exception as e:
            logger.error(f"{func.__name__} failed: {e}")
            raise
    return wrapper
