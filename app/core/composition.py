"""Composición de casos de uso y adaptadores a partir de la configuración."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass

import httpx

from app.adapters.outbound.media.ffmpeg_preparer import FfmpegPreparer
from app.adapters.outbound.providers.openrouter_model import OpenRouterModel
from app.adapters.outbound.providers.prueba_model import PruebaModel
from app.adapters.outbound.storage.http_evidence_reader import HttpEvidenceReader
from app.application.pipelines.media import (
    AUDIO_MIME_TYPES, IMAGE_MIME_TYPES, VIDEO_MIME_TYPES, MediaPolicy, SilencePolicy,
)
from app.application.ports.media_preparer import MediaPreparer
from app.application.ports.multimodal_model import MultimodalModel
from app.application.prompts import load_prompt
from app.application.structured_output import AudioOutput, EvidenceOutput, VideoOutput
from app.application.use_cases.analyze_evidence import AnalyzeEvidence, FrameSampling, ModalityProfile
from app.application.use_cases.synthesize_summary import SynthesizeIncidentSummary
from app.core.config import Settings
from app.domain.evidence_reference import Modality

logger = logging.getLogger(__name__)


def build_preparer(config: Settings) -> MediaPreparer | None:
    """ffprobe/ffmpeg si están instalados; sin ellos la duración se lee de la cabecera, el video va entero
    y no hay control de silencio."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        logger.warning("ffmpeg/ffprobe no están instalados: duración por cabecera, video completo "
                       "y sin control de silencio del audio")
        return None
    return FfmpegPreparer(config.ffmpeg_timeout_seconds, config.max_concurrent_model_calls)


@dataclass(frozen=True, slots=True)
class Services:
    analyze: AnalyzeEvidence
    synthesize: SynthesizeIncidentSummary


def build_services(config: Settings, client: httpx.AsyncClient) -> Services | None:
    """`None` si falta la clave de OpenRouter: el servicio arranca, pero responde 503.

    Con `provider="prueba"` no hace falta clave: todas las tareas usan el analizador de prueba.
    """
    if config.provider == "openrouter" and not config.openrouter_api_key:
        return None
    limiter = asyncio.Semaphore(config.max_concurrent_model_calls)

    def model(name: str | None) -> MultimodalModel:
        if config.provider == "prueba":
            return PruebaModel()
        return OpenRouterModel(config.openrouter_api_key, name or config.openrouter_model, client,
                               limiter=limiter, timeout=config.provider_timeout_seconds)

    profiles = {
        Modality.IMAGE: ModalityProfile(
            MediaPolicy(Modality.IMAGE, IMAGE_MIME_TYPES, config.image_max_bytes),
            load_prompt("image_v4"), EvidenceOutput, model(config.image_model),
        ),
        Modality.AUDIO: ModalityProfile(
            MediaPolicy(Modality.AUDIO, AUDIO_MIME_TYPES, config.audio_max_bytes, config.audio_max_seconds),
            load_prompt("audio_v4"), AudioOutput, model(config.audio_model),
        ),
        Modality.VIDEO: ModalityProfile(
            MediaPolicy(Modality.VIDEO, VIDEO_MIME_TYPES, config.video_max_bytes, config.video_max_seconds),
            load_prompt("video_v4"), VideoOutput, model(config.video_model),
            FrameSampling(load_prompt("video_v3"), config.video_max_frames, config.video_scene_threshold,
                          config.video_fallback_to_full) if config.video_mode == "frames" else None,
        ),
    }
    reader = HttpEvidenceReader(client, timeout=config.download_timeout_seconds)
    return Services(
        analyze=AnalyzeEvidence(
            reader, profiles, preparer=build_preparer(config),
            silence=SilencePolicy(config.silence_max_volume_db, config.silence_min_audible_seconds),
        ),
        synthesize=SynthesizeIncidentSummary(model(config.summary_model), load_prompt("summary_v2")),
    )
