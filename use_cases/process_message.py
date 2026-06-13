from typing import Dict, Any
from modules.database import db
from services.llm_router import llm_router

class ProcessMessageUseCase:
    async def execute(self, user_id: int, text: str) -> Dict[str, Any]:
        # Получаем историю
        history = await db.get_history(user_id, 20)
        await db.add_history(user_id, "user", text)
        history.append({"role": "user", "content": text})
        
        # Получаем модель пользователя
        model = await db.get_user_model(user_id)
        
        # Отправляем запрос
        result, used_model = await llm_router.route(model, history)
        
        # Сохраняем ответ
        await db.add_history(user_id, "assistant", result, used_model)
        
        return {
            "response": result,
            "model_used": used_model,
            "user_id": user_id
        }
