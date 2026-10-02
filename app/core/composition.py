"""Composición de casos de uso y adaptadores a partir de la configuración."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.adapters.outbound.providers.openrouter_model import OpenRouterModel
from app.adapters.outbound.storage.http_evidence_reader import HttpEvidenceReader
from app.application.pipelines.media import AUDIO_MIME_TYPES, IMAGE_MIME_TYPES, VIDEO_MIME_TYPES, MediaPolicy
from app.application.prompts import load_prompt
from app.application.structured_output import AudioOutput, EvidenceOutput, VideoOutput
from app.application.use_cases.analyze_evidence import AnalyzeEvidence, ModalityProfile
from app.application.use_cases.synthesize_summary import SynthesizeIncidentSummary
from app.core.config import Settings
from app.domain.evidence_reference import Modality


@dataclass(frozen=True, slots=True)
class Services:
    analyze: AnalyzeEvidence
    synthesize: SynthesizeIncidentSummary


def build_services(config: Settings, client: httpx.AsyncClient) -> Services | None:
    """`None` si falta la clave del proveedor: el servicio arranca, pero responde 503."""
    if not config.openrouter_api_key:
        return None
    limiter = asyncio.Semaphore(config.max_concurrent_model_calls)

    def model(name: str | None) -> OpenRouterModel:
        return OpenRouterModel(config.openrouter_api_key, name or config.openrouter_model, client,
                               limiter=limiter, timeout=config.provider_timeout_seconds)

    profiles = {
        Modality.IMAGE: ModalityProfile(
            MediaPolicy(Modality.IMAGE, IMAGE_MIME_TYPES, config.image_max_bytes),
            load_prompt("image_v2"), EvidenceOutput, model(config.image_model),
        ),
        Modality.AUDIO: ModalityProfile(
            MediaPolicy(Modality.AUDIO, AUDIO_MIME_TYPES, config.audio_max_bytes, config.audio_max_seconds),
            load_prompt("audio_v1"), AudioOutput, model(config.audio_model),
        ),
        Modality.VIDEO: ModalityProfile(
            MediaPolicy(Modality.VIDEO, VIDEO_MIME_TYPES, config.video_max_bytes, config.video_max_seconds),
            load_prompt("video_v1"), VideoOutput, model(config.video_model),
        ),
    }
    reader = HttpEvidenceReader(client, timeout=config.download_timeout_seconds)
    return Services(
        analyze=AnalyzeEvidence(reader, profiles),
        synthesize=SynthesizeIncidentSummary(model(config.summary_model), load_prompt("summary_v1")),
    )
