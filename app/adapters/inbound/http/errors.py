"""Formato único de error: `{"errorCode": ..., "retryable": ...}`.

Los errores de validación no repiten la entrada: la URL de descarga es un secreto temporal.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import AiError

logger = logging.getLogger(__name__)

STATUS = {
    "unauthorized": 401,
    "service_not_configured": 503,
    "provider_misconfigured": 503,
    "provider_rejected": 502,
    "internal_error": 500,
}


def status_for(error: AiError) -> int:
    return STATUS.get(error.code, 503 if error.retryable else 422)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AiError)
    async def ai_error(_: Request, exc: AiError) -> JSONResponse:
        return JSONResponse({"errorCode": exc.code, "retryable": exc.retryable}, status_code=status_for(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()})
        return JSONResponse({"errorCode": "invalid_request", "retryable": False, "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Fallo inesperado (%s)", type(exc).__name__)
        return JSONResponse({"errorCode": "internal_error", "retryable": True}, status_code=500)
