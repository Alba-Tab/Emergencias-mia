"""Ejecuta un trabajo aceptado y reporta su estado final."""

import logging

from app.application.use_cases.analyze_evidence import AnalyzeEvidence, UnsupportedModalityError
from app.domain.evidence_reference import EvidenceReference

logger = logging.getLogger(__name__)


class JobError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EvidenceJobRunner:
    def __init__(self, use_case: AnalyzeEvidence, sink) -> None:
        self.use_case = use_case
        self.sink = sink

    async def run(self, job_id: str, evidence: EvidenceReference) -> None:
        try:
            await self.use_case.execute(job_id, evidence)
        except UnsupportedModalityError:
            await self._report_failure(job_id, evidence, "unsupported_media_type")
        except JobError as exc:
            if exc.code == "callback_failed":
                logger.error("No se pudo entregar callback del trabajo %s", job_id)
                return
            await self._report_failure(job_id, evidence, exc.code)
        except ValueError:
            await self._report_failure(job_id, evidence, "invalid_evidence")
        except Exception as exc:
            logger.error("Fallo inesperado en el trabajo %s (%s)", job_id, type(exc).__name__)
            await self._report_failure(job_id, evidence, "internal_error")

    async def _report_failure(self, job_id: str, evidence: EvidenceReference, code: str) -> None:
        try:
            await self.sink.publish_failure(job_id, evidence, code)
        except Exception as exc:
            logger.error("No se pudo entregar el fallo del trabajo %s (%s)", job_id, type(exc).__name__)
