"""Analiza una sola evidencia: lee, valida y pide al modelo una salida estructurada.

Un audio sin sonido audible no se envía al modelo: con silencio puede inventar una transcripción
verosímil. El silencio se mide antes con el preparador (ffmpeg) y la decisión es determinista.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.application.pipelines.media import MediaPolicy, SilencePolicy, modality_of
from app.application.ports.evidence_reader import EvidenceReader
from app.application.ports.media_preparer import MediaInfo, MediaPreparationError, MediaPreparer, PreparedVideo
from app.application.ports.multimodal_model import MediaPart, MultimodalModel
from app.application.prompts import Prompt
from app.application.structured_output import EvidenceOutput, json_schema, parse_output
from app.domain.analysis_result import (
    MAX_ITEMS, AnalysisResult, EvidenceAnalysis, Provenance, Severity, TimedObservation,
)
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference, Modality

logger = logging.getLogger(__name__)

_LABELS = {Modality.IMAGE: "una imagen", Modality.AUDIO: "un audio grabado", Modality.VIDEO: "un video"}
SILENT_METHOD = "silent_audio"  # resultado armado sin modelo porque el audio está en silencio
SILENT_LIMITATION = "El audio está en silencio o casi en silencio: no se puede saber qué pasa."
DROPPED_TRANSCRIPT = "El audio no tiene sonido audible: se descartó una transcripción que no puede provenir de él."
NO_SOUND = ("El video no tiene sonido audible (se midió antes de enviarlo): transcript debe ser null "
            "y no hay observaciones de lo que se oye.")


@dataclass(frozen=True, slots=True)
class FrameSampling:
    """Video por fotogramas: qué prompt usar, cuántos fotogramas y qué hacer si no se puede preparar."""

    prompt: Prompt
    max_frames: int = 8
    scene_threshold: float = 0.3
    fallback_to_full: bool = True


@dataclass(frozen=True, slots=True)
class ModalityProfile:
    """Lo único que distingue a imagen, audio y video: límites, prompt, esquema y modelo."""

    policy: MediaPolicy
    prompt: Prompt
    output: type[EvidenceOutput]
    model: MultimodalModel
    frames: FrameSampling | None = None


def snap_timeline(analysis: EvidenceAnalysis, seconds: tuple[float, ...]) -> EvidenceAnalysis:
    """Cada momento de la línea de tiempo pasa al segundo de fotograma más cercano.

    El modelo solo vio esos instantes: un segundo distinto sería inventado.
    """
    if not seconds or not analysis.timeline:
        return analysis
    timeline = tuple(sorted(
        (TimedObservation(min(seconds, key=lambda s: (abs(s - t.start_second), s)), t.text) for t in analysis.timeline),
        key=lambda t: t.start_second,
    ))
    return replace(analysis, timeline=timeline)


def silent_audio_analysis() -> EvidenceAnalysis:
    return EvidenceAnalysis(
        summary="El audio no tiene sonido audible.",
        event_type="undetermined",
        people=None,
        hazards=(),
        observations=(),
        risks=(),
        severity=Severity("undetermined", ()),
        limitations=(SILENT_LIMITATION,),
        transcript=None,
    )


def without_transcript(analysis: EvidenceAnalysis) -> EvidenceAnalysis:
    """Se midió silencio: una transcripción del modelo no puede venir del audio y se descarta."""
    if analysis.transcript is None:
        return analysis
    logger.warning("el modelo devolvió una transcripción de un audio en silencio; se descarta")
    return replace(analysis, transcript=None, limitations=(*analysis.limitations[:MAX_ITEMS - 1], DROPPED_TRANSCRIPT))


class AnalyzeEvidence:
    def __init__(
        self,
        reader: EvidenceReader,
        profiles: Mapping[Modality, ModalityProfile],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        preparer: MediaPreparer | None = None,
        silence: SilencePolicy = SilencePolicy(),
    ) -> None:
        self.reader = reader
        self.profiles = dict(profiles)
        self.clock = clock
        self.preparer = preparer
        self.silence = silence

    async def execute(self, job_id: str, evidence: EvidenceReference) -> AnalysisResult:
        modality = modality_of(evidence.mime_type)
        profile = self.profiles.get(modality)
        if profile is None:
            raise AiError("unsupported_media_type")

        data = await self.reader.read(evidence, profile.policy.max_bytes)
        profile.policy.validate(data, evidence.mime_type, evidence.checksum_sha256)
        info = await self._measure(profile.policy, data, evidence.mime_type)
        silent = await self._is_silent(modality, data, evidence.mime_type, info)

        def result(analysis: EvidenceAnalysis, provenance: Provenance) -> AnalysisResult:
            return AnalysisResult(job_id, evidence.evidence_id, evidence.alert_id, evidence.incident_id,
                                  modality, analysis, provenance)

        if silent and modality is Modality.AUDIO:
            return result(silent_audio_analysis(), Provenance(None, None, None, self.clock(), SILENT_METHOD))

        prompt, media, seconds = profile.prompt, MediaPart(modality, evidence.mime_type, data), ()
        text = f"Analiza {_LABELS[modality]} enviada por un ciudadano junto a su alerta de emergencia."
        if silent:
            # La pista en silencio no se extrae ni se envía: solo los fotogramas.
            info = replace(info, has_audio=False)
            text = f"{text} {NO_SOUND}"
        prepared = await self._prepare(profile, data, evidence.mime_type, info)
        if prepared is not None:
            prompt, media, text = profile.frames.prompt, _frame_parts(prepared), _frames_text(prepared, silent)
            seconds = tuple(frame.second for frame in prepared.frames)

        reply = await profile.model.generate(
            instructions=prompt.text,
            text=text,
            media=media,
            schema_name=f"{modality.value}_evidence",
            schema=json_schema(profile.output),
        )
        analysis = snap_timeline(parse_output(profile.output, reply.content).to_domain(), seconds)
        if silent:
            analysis = without_transcript(analysis)
        return result(analysis, Provenance(reply.provider, reply.model, prompt.version, self.clock(), "model"))

    async def _measure(self, policy: MediaPolicy, data: bytes, mime_type: str) -> MediaInfo | None:
        """Duración real con el preparador; si la herramienta falla, queda el control por cabecera."""
        if self.preparer is None or policy.max_seconds is None:
            return None
        try:
            info = await self.preparer.probe(data, mime_type)
        except MediaPreparationError:
            logger.warning("no se pudo medir la duración con el preparador; se usa la de la cabecera")
            return None
        policy.check_duration(info.duration)
        return info

    async def _is_silent(self, modality: Modality, data: bytes, mime_type: str, info: MediaInfo | None) -> bool:
        """`True` solo si se midió que la pista de audio no tiene sonido audible.

        Sin preparador o si la medición falla no se puede saber: se analiza igual y lo cubre el prompt.
        """
        if modality is Modality.IMAGE or info is None or not info.has_audio:
            return False
        try:
            level = await self.preparer.measure_sound(data, mime_type, self.silence.max_volume_db)
        except MediaPreparationError:
            logger.warning("no se pudo medir el sonido; se analiza sin el control de silencio")
            return False
        return self.silence.is_silent(level)

    async def _prepare(
        self, profile: ModalityProfile, data: bytes, mime_type: str, info: MediaInfo | None,
    ) -> PreparedVideo | None:
        sampling = profile.frames
        if sampling is None or self.preparer is None or info is None or not info.has_video:
            if sampling is not None and not sampling.fallback_to_full:
                raise AiError("unreadable_media")
            return None
        try:
            return await self.preparer.prepare_video(
                data, mime_type, info, sampling.max_frames, sampling.scene_threshold)
        except MediaPreparationError:
            if not sampling.fallback_to_full:
                raise AiError("unreadable_media") from None
            logger.warning("no se pudieron extraer los fotogramas; se envía el video completo")
            return None


def _frame_parts(prepared: PreparedVideo) -> tuple[MediaPart, ...]:
    parts = [MediaPart(Modality.IMAGE, "image/jpeg", frame.data, frame.second) for frame in prepared.frames]
    if prepared.audio:
        parts.append(MediaPart(Modality.AUDIO, prepared.audio_mime_type, prepared.audio))
    return tuple(parts)


def _frames_text(prepared: PreparedVideo, silent: bool = False) -> str:
    seconds = ", ".join(f"{frame.second:g}" for frame in prepared.frames)
    if silent:
        sound = NO_SOUND
    elif prepared.audio:
        sound = "Después de los fotogramas va la pista de audio completa."
    else:
        sound = "El video no tiene audio."
    return (
        f"Analiza un video enviado por un ciudadano junto a su alerta de emergencia. Dura {prepared.duration:g} s. "
        f"Recibes {len(prepared.frames)} fotogramas en orden, de los segundos {seconds}. {sound}"
    )
