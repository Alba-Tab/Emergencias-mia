"""Síntesis síncrona del resumen de un incidente a partir de resultados ya analizados."""

import logging
import time

from fastapi import APIRouter, Depends

from app.adapters.inbound.http.dependencies import authorize, get_services
from app.core.composition import Services
from app.domain.errors import AiError
from app.schemas.summary import SummaryRequest, summary_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])


@router.post("/summaries")
async def summarize(request: SummaryRequest, services: Services = Depends(get_services)) -> dict:
    started = time.monotonic()
    outcome = "error"
    try:
        summary = await services.synthesize.execute(request.to_domain())
        outcome = summary.provenance.method
        return summary_response(summary)
    except AiError as exc:
        outcome = exc.code
        raise
    finally:
        logger.info("summary incident=%s alerts=%d evidences=%d outcome=%s ms=%d",
                    request.incidentId, len(request.alerts), len(request.evidences), outcome,
                    (time.monotonic() - started) * 1000)
