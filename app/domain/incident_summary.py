"""Síntesis del incidente a partir de resultados ya analizados y datos de las alertas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.analysis_result import (
    BASES, EVENT_TYPES, HAZARDS, EvidenceAnalysis, PeopleRange, Provenance, Severity, clip,
)
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality

MAX_ALERTS = 50
MAX_EVIDENCES = 50


@dataclass(frozen=True, slots=True)
class AlertContext:
    """Datos que escribió el ciudadano: útiles, pero no verificados."""

    alert_id: int
    reported_at: datetime | None
    description: str | None
    affected_count: int | None
    reporter_is_patient: bool | None

    @property
    def has_text(self) -> bool:
        return bool(self.description and self.description.strip()) or self.affected_count is not None


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    evidence_id: int
    alert_id: int
    modality: Modality
    received_at: datetime | None
    analysis: EvidenceAnalysis


@dataclass(frozen=True, slots=True)
class SynthesisInput:
    incident_id: int
    alerts: tuple[AlertContext, ...]
    evidences: tuple[EvidenceInput, ...]

    def __post_init__(self) -> None:
        alert_ids = [alert.alert_id for alert in self.alerts]
        evidence_ids = [evidence.evidence_id for evidence in self.evidences]
        if self.incident_id <= 0:
            raise AiError("invalid_request")
        if len(alert_ids) != len(set(alert_ids)) or len(evidence_ids) != len(set(evidence_ids)):
            raise AiError("duplicate_source")
        if len(alert_ids) > MAX_ALERTS or len(evidence_ids) > MAX_EVIDENCES:
            raise AiError("too_many_sources")
        if any(evidence.alert_id not in alert_ids for evidence in self.evidences):
            raise AiError("unknown_alert")
        if not self.evidences and not any(alert.has_text for alert in self.alerts):
            raise AiError("nothing_to_summarize")

    @property
    def is_single_evidence(self) -> bool:
        """Una sola evidencia y ningún texto de alerta: no hay nada que combinar."""
        return len(self.evidences) == 1 and not any(alert.has_text for alert in self.alerts)

    def alert_of(self, evidence_id: int) -> int:
        return next(e.alert_id for e in self.evidences if e.evidence_id == evidence_id)

    def sourced(self, text: str, basis: str, evidence_ids, alert_ids) -> SourcedStatement:
        """Comprueba que el modelo cite solo fuentes recibidas y calcula la corroboración."""
        evidence_ids = tuple(sorted(set(evidence_ids)))
        alert_ids = tuple(sorted(set(alert_ids)))
        known_evidences = {e.evidence_id for e in self.evidences}
        textual_alerts = {a.alert_id for a in self.alerts if a.has_text}
        if not evidence_ids and not alert_ids:
            raise AiError("invalid_model_output", retryable=True)
        if not set(evidence_ids) <= known_evidences or not set(alert_ids) <= textual_alerts:
            raise AiError("invalid_model_output", retryable=True)
        alerts = set(alert_ids) | {self.alert_of(evidence_id) for evidence_id in evidence_ids}
        return SourcedStatement(clip(text), basis, evidence_ids, alert_ids, len(alerts))


@dataclass(frozen=True, slots=True)
class SourcedStatement:
    """`corroborating_alerts` cuenta alertas distintas que respaldan la afirmación.

    Es la medida de respaldo que se publica en lugar de una confianza numérica inventada.
    """

    text: str
    basis: str
    evidence_ids: tuple[int, ...]
    alert_ids: tuple[int, ...]
    corroborating_alerts: int

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError("basis desconocido")


@dataclass(frozen=True, slots=True)
class IncidentSummary:
    incident_id: int
    summary: str
    event_type: str
    people: PeopleRange | None
    hazards: tuple[str, ...]
    findings: tuple[SourcedStatement, ...]
    risks: tuple[SourcedStatement, ...]
    severity: Severity
    conflicts: tuple[SourcedStatement, ...]
    limitations: tuple[str, ...]
    used_evidence_ids: tuple[int, ...]
    used_alert_ids: tuple[int, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("summary es obligatorio")
        if self.event_type not in EVENT_TYPES or any(h not in HAZARDS for h in self.hazards):
            raise ValueError("vocabulario desconocido")


def used_sources(source: SynthesisInput) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return (
        tuple(sorted(e.evidence_id for e in source.evidences)),
        tuple(sorted(a.alert_id for a in source.alerts)),
    )


def single_evidence_summary(source: SynthesisInput, generated_at: datetime) -> IncidentSummary:
    """Traslada el único resultado al formato del resumen sin llamar al modelo."""
    evidence = source.evidences[0]
    analysis = evidence.analysis
    ids = (evidence.evidence_id,)
    used_evidences, used_alerts = used_sources(source)
    return IncidentSummary(
        incident_id=source.incident_id,
        summary=analysis.summary,
        event_type=analysis.event_type,
        people=analysis.people,
        hazards=analysis.hazards,
        findings=tuple(SourcedStatement(f.text, f.basis, ids, (), 1) for f in analysis.observations),
        risks=tuple(SourcedStatement(risk, "inferred", ids, (), 1) for risk in analysis.risks),
        severity=analysis.severity,
        conflicts=(),
        limitations=analysis.limitations,
        used_evidence_ids=used_evidences,
        used_alert_ids=used_alerts,
        provenance=Provenance(None, None, None, generated_at, "single_evidence"),
    )
