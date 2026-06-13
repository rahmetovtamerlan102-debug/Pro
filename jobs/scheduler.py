import asyncio
import logging
from datetime import datetime
from typing import Callable, Any

logger = logging.getLogger(__name__)

class JobScheduler:
    _jobs = []
    
    @classmethod
    def add_job(cls, func: Callable, interval: int, *args, **kwargs):
        cls._jobs.append({
            "func": func,
            "interval": interval,
            "args": args,
            "kwargs": kwargs,
            "last_run": None
        })
    
    @classmethod
    async def start(cls):
        while True:
            now = datetime.now()
            for job in cls._jobs:
                if job["last_run"] is None or (now - job["last_run"]).total_seconds() >= job["interval"]:
                    try:
                        if asyncio.iscoroutinefunction(job["func"]):
                            await job["func"](*job["args"], **job["kwargs"])
                        else:
                            job["func"](*job["args"], **job["kwargs"])
                    except Exception as e:
                        logger.error(f"Job failed: {e}")
                    job["last_run"] = now
            await asyncio.sleep(1)
