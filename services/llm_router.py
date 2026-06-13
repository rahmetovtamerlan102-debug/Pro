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
        self.fallback_models = config.MODEL_LIST[1:] if len(config.MODEL_LIST) > 1 else []
    
    async def ask_groq(self, model: str, messages: list) -> tuple:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": 0.5, "max_tokens": 1500}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"], model
                else:
                    logger.error(f"Groq API error: {response.status_code}")
                    return None, None
            except Exception as e:
                logger.error(f"Groq exception: {e}")
                return None, None
    
    async def route(self, model: str, messages: list) -> dict:
        # Пробуем основную модель
        try:
            result, used_model = await self.groq_cb.call(self.ask_groq, model, messages)
            if result:
                return {"response": result, "model": used_model}
        except Exception as e:
            logger.warning(f"Primary model failed: {e}")
        
        # Fallback на другие модели
        for fallback in self.fallback_models:
            if fallback == model:
                continue
            try:
                result, used_model = await self.ask_groq(fallback, messages)
                if result:
                    model_name = config.MODEL_NAMES.get(fallback, fallback)
                    return {"response": f"[Fallback: {model_name}]\n\n{result}", "model": fallback}
            except Exception as e:
                logger.warning(f"Fallback {fallback} failed: {e}")
                continue
        
        return {"response": "Сервис временно недоступен. Попробуйте позже.", "model": "error"}

# Создаём экземпляр роутера
router = LLMRouter()
llm_router = router  # <-- для импорта из других модулей

@app.post("/process")
async def process(request: dict):
    model = request.get("model", config.MODEL_LIST[0] if config.MODEL_LIST else "")
    messages = request.get("messages", [])
    result = await router.route(model, messages)
    return result

@app.get("/health")
async def health():
    return {"status": "ok", "service": "llm-router"}

@app.get("/metrics")
async def metrics():
    return {
        "service": "llm-router",
        "status": "running",
        "models": config.MODEL_LIST
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
