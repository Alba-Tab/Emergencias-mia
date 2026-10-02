"""Esquemas que se exigen al modelo y su conversión al dominio.

El JSON Schema se envía sin `$ref` ni títulos para maximizar la compatibilidad entre proveedores;
la respuesta se valida de nuevo localmente, porque el modo estricto no lo garantizan todos.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.analysis_result import (
    MAX_ITEMS, MAX_TRANSCRIPT, Basis, EventType, EvidenceAnalysis, Finding, Hazard, PeopleRange,
    Severity, SeverityLevel, TimedObservation, clip, clip_all,
)
from app.domain.errors import AiError
from app.domain.incident_summary import SynthesisInput

logger = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _not_blank(value: str) -> str:
    """Un `summary` de solo espacios es una salida inválida del modelo, no un error interno."""
    if not value.strip():
        raise ValueError("summary vacío")
    return value


class FindingOut(_Strict):
    text: str
    basis: Basis


class EvidenceOutput(_Strict):
    summary: str = Field(min_length=1)
    eventType: EventType
    peopleMin: int | None
    peopleMax: int | None
    hazards: list[Hazard]
    observations: list[FindingOut]
    risks: list[str]
    severity: SeverityLevel
    severityBasis: list[str]
    limitations: list[str]

    _summary = field_validator("summary")(_not_blank)

    def _common(self, transcript: str | None = None, timeline=()) -> EvidenceAnalysis:
        return EvidenceAnalysis(
            summary=clip(self.summary),
            event_type=self.eventType,
            people=PeopleRange.of(self.peopleMin, self.peopleMax),
            hazards=tuple(dict.fromkeys(self.hazards)),
            observations=tuple(Finding(clip(o.text), o.basis) for o in self.observations if o.text.strip())[:MAX_ITEMS],
            risks=clip_all(self.risks),
            severity=Severity.assess(self.severity, self.severityBasis),
            limitations=clip_all(self.limitations),
            transcript=transcript,
            timeline=timeline,
        )

    def to_domain(self) -> EvidenceAnalysis:
        return self._common()


class AudioOutput(EvidenceOutput):
    transcript: str

    def to_domain(self) -> EvidenceAnalysis:
        return self._common(transcript=clip(self.transcript, MAX_TRANSCRIPT))


class TimedOut(_Strict):
    startSecond: float = Field(ge=0)
    text: str


class VideoOutput(EvidenceOutput):
    transcript: str
    timeline: list[TimedOut]

    def to_domain(self) -> EvidenceAnalysis:
        timeline = tuple(sorted(
            (TimedObservation(t.startSecond, clip(t.text)) for t in self.timeline if t.text.strip()),
            key=lambda t: t.start_second,
        ))[:MAX_ITEMS]
        return self._common(transcript=clip(self.transcript, MAX_TRANSCRIPT), timeline=timeline)


class SourcedOut(_Strict):
    text: str
    evidenceIds: list[int]
    alertIds: list[int]


class SourcedFindingOut(SourcedOut):
    basis: Basis


class SummaryOutput(_Strict):
    summary: str = Field(min_length=1)
    eventType: EventType
    peopleMin: int | None
    peopleMax: int | None
    hazards: list[Hazard]
    findings: list[SourcedFindingOut]
    risks: list[SourcedOut]
    severity: SeverityLevel
    severityBasis: list[str]
    conflicts: list[SourcedOut]
    limitations: list[str]

    _summary = field_validator("summary")(_not_blank)

    def statements(self, source: SynthesisInput):
        """Devuelve hallazgos, riesgos y contradicciones con sus fuentes verificadas.

        Una cita a una fuente que no se recibió se descarta; la afirmación que se queda sin
        ninguna fuente válida también. El resto del resumen se conserva.
        """
        dropped = {"citations": 0, "items": 0}

        def keep(items, basis_of):
            kept = []
            for item in items:
                if not item.text.strip():
                    continue
                statement, invalid = source.sourced(item.text, basis_of(item), item.evidenceIds, item.alertIds)
                dropped["citations"] += invalid
                if statement is None:
                    dropped["items"] += 1
                else:
                    kept.append(statement)
            return tuple(kept)[:MAX_ITEMS]

        findings = keep(self.findings, lambda f: f.basis)
        risks = keep(self.risks, lambda _: "inferred")
        conflicts = keep(self.conflicts, lambda _: "inferred")
        if dropped["citations"] or dropped["items"]:
            # Solo cantidades: el texto puede traer datos del ciudadano.
            logger.warning("resumen %s: citas inválidas descartadas=%d, afirmaciones sin fuente descartadas=%d",
                           source.incident_id, dropped["citations"], dropped["items"])
        return findings, risks, conflicts


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    definitions = raw.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: resolve(value) for key, value in node.items() if key not in {"title", "default"}}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(raw)


def parse_output(model: type[BaseModel], content: dict[str, Any]):
    try:
        return model.model_validate(content)
    except ValidationError as exc:
        raise AiError("invalid_model_output", retryable=True) from exc
