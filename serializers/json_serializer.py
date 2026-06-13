import json
from typing import Any

class JSONSerializer:
    @staticmethod
    def serialize(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)
    
    @staticmethod
    def deserialize(data: str) -> Any:
        return json.loads(data)
    
    @staticmethod
    async def serialize_async(data: Any) -> str:
        return JSONSerializer.serialize(data)
    
    @staticmethod
    async def deserialize_async(data: str) -> Any:
        return JSONSerializer.deserialize(data)
