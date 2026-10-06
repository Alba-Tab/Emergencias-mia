"""Contrato HTTP de `POST /v1/analyses` y forma pública del resultado individual."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.net import valid_download_url
from app.domain.analysis_result import AnalysisResult, EvidenceAnalysis, Provenance
from app.domain.evidence_reference import EvidenceReference

# v2 agrega `usable` y `unusableReason`; los campos de v1 no cambian.
EVIDENCE_SCHEMA_VERSION = "evidence-analysis.v2"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: UUID
    evidenceId: int = Field(gt=0)
    alertId: int = Field(gt=0)
    incidentId: int = Field(gt=0)
    downloadUrl: str = Field(min_length=1, max_length=4096)
    mimeType: str = Field(min_length=3, max_length=100)
    checksumSha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("downloadUrl")
    @classmethod
    def _safe_url(cls, value: str) -> str:
        if not valid_download_url(value):
            raise ValueError("downloadUrl debe ser HTTPS público")
        return value

    @field_validator("mimeType")
    @classmethod
    def _normalize_mime(cls, value: str) -> str:
        return value.split(";", 1)[0].strip().lower()

    def to_domain(self) -> EvidenceReference:
        return EvidenceReference(
            self.evidenceId, self.alertId, self.incidentId, self.downloadUrl, self.mimeType,
            self.checksumSha256.lower(),
        )


def provenance_json(provenance: Provenance) -> dict[str, Any]:
    return {
        "provider": provenance.provider,
        "model": provenance.model,
        "promptVersion": provenance.prompt_version,
        "generatedAt": provenance.generated_at.isoformat(),
        "method": provenance.method,
    }


def analysis_json(analysis: EvidenceAnalysis) -> dict[str, Any]:
    return {
        "summary": analysis.summary,
        "eventType": analysis.event_type,
        "people": {"min": analysis.people.minimum, "max": analysis.people.maximum} if analysis.people else None,
        "hazards": list(analysis.hazards),
        "observations": [{"text": f.text, "basis": f.basis} for f in analysis.observations],
        "risks": list(analysis.risks),
        "severity": {"level": analysis.severity.level, "basis": list(analysis.severity.basis)},
        "limitations": list(analysis.limitations),
        "transcript": analysis.transcript,
        "timeline": [{"startSecond": t.start_second, "text": t.text} for t in analysis.timeline],
        "usable": analysis.usable,
        "unusableReason": analysis.unusable_reason,
    }


def analysis_response(result: AnalysisResult) -> dict[str, Any]:
    return {
        "jobId": result.job_id,
        "evidenceId": result.evidence_id,
        "alertId": result.alert_id,
        "incidentId": result.incident_id,
        "modality": result.modality.value,
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "analysis": analysis_json(result.analysis),
        "provenance": provenance_json(result.provenance),
    }
