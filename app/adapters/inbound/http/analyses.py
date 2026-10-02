"""Análisis síncrono de una evidencia: la respuesta trae el resultado o un error con `retryable`."""

import logging
import time

from fastapi import APIRouter, Depends

from app.adapters.inbound.http.dependencies import authorize, get_services
from app.core.composition import Services
from app.domain.errors import AiError
from app.schemas.analysis import AnalysisRequest, analysis_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])


@router.post("/analyses")
async def analyze(request: AnalysisRequest, services: Services = Depends(get_services)) -> dict:
    started = time.monotonic()
    outcome = "error"
    try:
        result = await services.analyze.execute(str(request.jobId), request.to_domain())
        outcome = "ok"
        return analysis_response(result)
    except AiError as exc:
        outcome = exc.code
        raise
    finally:
        # Correlación sin URL ni bytes: la URL de descarga es un secreto temporal.
        logger.info("analysis job=%s evidence=%s incident=%s mime=%s outcome=%s ms=%d",
                    request.jobId, request.evidenceId, request.incidentId, request.mimeType, outcome,
                    (time.monotonic() - started) * 1000)
