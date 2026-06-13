import time
import logging
from typing import Callable, Any
from functools import wraps

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, cooldown: int = 30):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.failures = 0
        self.last_failure_time = 0
        self.state = "closed"
    
    def is_open(self) -> bool:
        if self.state == "open":
            if time.time() - self.last_failure_time > self.cooldown:
                self.state = "half_open"
                logger.info(f"Circuit breaker {self.name} -> half_open")
                return False
            return True
        return False
    
    def record_success(self):
        if self.state == "half_open":
            self.state = "closed"
            self.failures = 0
            logger.info(f"Circuit breaker {self.name} -> closed")
    
    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker {self.name} -> open")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        if self.is_open():
            raise Exception(f"Circuit breaker {self.name} is open")
        
        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise e
