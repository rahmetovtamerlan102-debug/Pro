import aiohttp
import logging
from typing import List, Dict, Optional
from modules.config import config

logger = logging.getLogger(__name__)

class OpenRouterAdapter:
    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.base_url = "https://openrouter.ai/api/v1"
    
    async def chat_completion(self, model: str, messages: List[Dict]) -> Optional[str]:
        if not self.api_key:
            return None
        
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": 0.5, "max_tokens": 1500}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    return None
            except:
                return None
