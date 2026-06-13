from datetime import datetime, timedelta
from typing import Optional

class TimeHelper:
    @staticmethod
    def now() -> datetime:
        return datetime.now()
    
    @staticmethod
    def to_iso(dt: datetime) -> str:
        return dt.isoformat()
    
    @staticmethod
    def from_iso(iso_string: str) -> datetime:
        return datetime.fromisoformat(iso_string)
    
    @staticmethod
    def add_days(dt: datetime, days: int) -> datetime:
        return dt + timedelta(days=days)
    
    @staticmethod
    def format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.1f}m"
        else:
            return f"{seconds / 3600:.1f}h"
