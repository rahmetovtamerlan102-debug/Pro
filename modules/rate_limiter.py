import time
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from modules.config import config

class RateLimiter:
    def __init__(self):
        self.memory_tracker: Dict[int, List[float]] = defaultdict(list)
        self.warned_users: set = set()
    
    def check(self, user_id: int) -> Tuple[bool, Optional[str]]:
        now = time.time()
        
        self.memory_tracker[user_id] = [t for t in self.memory_tracker[user_id] if now - t < config.SPAM_WINDOW]
        self.memory_tracker[user_id].append(now)
        
        count = len(self.memory_tracker[user_id])
        
        if count >= config.SPAM_THRESHOLD:
            return True, "Вы забанены за спам (50 запросов в минуту)"
        
        if count >= config.SPAM_WARN_THRESHOLD and user_id not in self.warned_users:
            self.warned_users.add(user_id)
            return False, "⚠️ Предупреждение: слишком много запросов!"
        
        return False, None

rate_limiter = RateLimiter()
