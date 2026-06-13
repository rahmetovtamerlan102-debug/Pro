from fastapi import APIRouter
from modules.database import db
from modules.redis_client import redis_manager
from modules.config import config

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

@router.get("/health")
async def health_check():
    """Базовая проверка здоровья"""
    return {"status": "ok", "service": config.SERVICE_NAME}

@router.get("/health/deep")
async def deep_health_check():
    """Глубокая проверка всех компонентов"""
    checks = {}
    
    # Проверка PostgreSQL
    try:
        await db.get_total_users()
        checks["postgresql"] = "healthy"
    except Exception as e:
        checks["postgresql"] = f"unhealthy: {e}"
    
    # Проверка Redis
    if redis_manager.enabled:
        try:
            await redis_manager.client.ping()
            checks["redis"] = "healthy"
        except:
            checks["redis"] = "unhealthy"
    else:
        checks["redis"] = "disabled"
    
    # Общий статус
    all_healthy = all(v == "healthy" for v in checks.values())
    
    return {
        "status": "healthy" if all_healthy else "degraded",
        "timestamp": datetime.now().isoformat(),
        "checks": checks
    }

@router.get("/metrics/queue")
async def queue_metrics():
    """Метрики очереди"""
    from services.message_router import message_router
    
    return {
        "queue_size": message_router.queue.qsize(),
        "max_size": config.QUEUE_MAX_SIZE,
        "workers": config.QUEUE_WORKERS
    }
