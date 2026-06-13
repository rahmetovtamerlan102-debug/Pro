import aiohttp
import logging
from typing import List, Dict, Optional
from modules.config import config

logger = logging.getLogger(__name__)

class GroqAdapter:
    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        self.base_url = "https://api.groq.com/openai/v1"
    
    async def chat_completion(self, model: str, messages: List[Dict], temperature: float = 0.5) -> Optional[str]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": 1500}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"Groq API error: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"Groq exception: {e}")
                return None
