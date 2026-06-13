import httpx
import logging
from fastapi import FastAPI
from modules.config import config
from modules.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Router")

class LLMRouter:
    def __init__(self):
        self.groq_cb = CircuitBreaker("groq")
        self.fallback_models = config.MODEL_LIST[1:]
    
    async def ask_groq(self, model: str, messages: list) -> tuple:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": 0.5, "max_tokens": 1500}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"], model
        return None, None
    
    async def route(self, model: str, messages: list) -> dict:
        try:
            result, used_model = await self.groq_cb.call(self.ask_groq, model, messages)
            if result:
                return {"response": result, "model": used_model}
        except Exception as e:
            logger.warning(f"Primary model failed: {e}")
        
        for fallback in self.fallback_models:
            try:
                result, used_model = await self.ask_groq(fallback, messages)
                if result:
                    return {"response": f"[Fallback: {config.MODEL_NAMES[fallback]}]\n\n{result}", "model": fallback}
            except:
                continue
        
        return {"response": "Service unavailable", "model": "error"}

router = LLMRouter()

@app.post("/process")
async def process(request: dict):
    model = request.get("model", config.MODEL_LIST[0])
    messages = request.get("messages", [])
    result = await router.route(model, messages)
    return result

@app.get("/health")
async def health():
    return {"status": "ok", "service": "llm-router"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
