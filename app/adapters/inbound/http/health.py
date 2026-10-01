from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])

@router.get("")
async def check_health():
    """
    Endpoint base para verificar que el servicio está vivo.
    """
    return {"status": "ok", "service": "emergencias-ai"}
