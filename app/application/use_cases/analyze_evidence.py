"""Analiza una sola evidencia: lee, valida y pide al modelo una salida estructurada."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.pipelines.media import MediaPolicy, modality_of
from app.application.ports.evidence_reader import EvidenceReader
from app.application.ports.multimodal_model import MediaPart, MultimodalModel
from app.application.prompts import Prompt
from app.application.structured_output import EvidenceOutput, json_schema, parse_output
from app.domain.analysis_result import AnalysisResult, Provenance
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference, Modality

_LABELS = {Modality.IMAGE: "una imagen", Modality.AUDIO: "un audio grabado", Modality.VIDEO: "un video"}


@dataclass(frozen=True, slots=True)
class ModalityProfile:
    """Lo único que distingue a imagen, audio y video: límites, prompt, esquema y modelo."""

    policy: MediaPolicy
    prompt: Prompt
    output: type[EvidenceOutput]
    model: MultimodalModel


class AnalyzeEvidence:
    def __init__(
        self,
        reader: EvidenceReader,
        profiles: Mapping[Modality, ModalityProfile],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.reader = reader
        self.profiles = dict(profiles)
        self.clock = clock

    async def execute(self, job_id: str, evidence: EvidenceReference) -> AnalysisResult:
        modality = modality_of(evidence.mime_type)
        profile = self.profiles.get(modality)
        if profile is None:
            raise AiError("unsupported_media_type")

        data = await self.reader.read(evidence, profile.policy.max_bytes)
        profile.policy.validate(data, evidence.mime_type, evidence.checksum_sha256)

        reply = await profile.model.generate(
            instructions=profile.prompt.text,
            text=f"Analiza {_LABELS[modality]} enviada por un ciudadano junto a su alerta de emergencia.",
            media=MediaPart(modality, evidence.mime_type, data),
            schema_name=f"{modality.value}_evidence",
            schema=json_schema(profile.output),
        )
        analysis = parse_output(profile.output, reply.content).to_domain()
        return AnalysisResult(
            job_id=job_id,
            evidence_id=evidence.evidence_id,
            alert_id=evidence.alert_id,
            incident_id=evidence.incident_id,
            modality=modality,
            analysis=analysis,
            provenance=Provenance(reply.provider, reply.model, profile.prompt.version, self.clock(), "model"),
        )
