"""Aceptación autenticada de trabajos de imagen."""

import hmac
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from app.adapters.outbound.backend.http_result_sink import HttpResultSink
from app.adapters.outbound.providers.openrouter_scene_analyzer import OpenRouterSceneAnalyzer
from app.adapters.outbound.storage.s3_evidence_reader import S3EvidenceReader
from app.application.job_runner import EvidenceJobRunner
from app.application.use_cases.analyze_evidence import AnalyzeEvidence
from app.application.use_cases.analyze_image import AnalyzeImage
from app.core.config import Settings, settings
from app.domain.evidence_reference import EvidenceReference
from app.schemas.job import JobRequest

router = APIRouter(prefix="/v1")


def get_settings() -> Settings:
    return settings


def authorize(authorization: str | None = Header(default=None), config: Settings = Depends(get_settings)) -> Settings:
    if not config.service_token:
        raise HTTPException(status_code=503, detail="service_not_configured")
    expected = f"Bearer {config.service_token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return config


def _safe_callback_url(url: str) -> bool:
    parsed = urlsplit(url)
    return bool(parsed.hostname) and (
        parsed.scheme == "https" or
        (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    )


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(job: JobRequest, background_tasks: BackgroundTasks, config: Settings = Depends(authorize)) -> dict:
    if not job.is_supported_image or job.bucket != config.s3_bucket:
        raise HTTPException(status_code=422, detail="invalid_evidence")
    if not all((config.openrouter_api_key, config.callback_url, config.callback_token)):
        raise HTTPException(status_code=503, detail="service_not_configured")
    if not _safe_callback_url(config.callback_url):
        raise HTTPException(status_code=503, detail="invalid_callback_url")
    evidence = EvidenceReference(
        job.evidence_id, job.alert_id, job.incident_id, job.bucket,
        job.object_key, job.mime_type, job.checksum_sha256.lower(),
    )
    reader = S3EvidenceReader(config.s3_bucket, config.aws_region)
    analyzer = OpenRouterSceneAnalyzer(config.openrouter_api_key, config.openrouter_model, timeout=config.openrouter_timeout_seconds)
    sink = HttpResultSink(config.callback_url, config.callback_token, timeout=config.callback_timeout_seconds)
    image_analysis = AnalyzeImage(reader, analyzer)
    runner = EvidenceJobRunner(AnalyzeEvidence({"image": image_analysis}, sink), sink)
    background_tasks.add_task(runner.run, str(job.job_id), evidence)
    return {"jobId": str(job.job_id), "status": "accepted"}
