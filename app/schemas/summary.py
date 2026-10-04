"""Contrato HTTP de `POST /v1/summaries`.

`analysis` es exactamente el objeto que devolvió `POST /v1/analyses`; el backend lo guarda tal
cual y lo reenvía aquí, sin interpretar su contenido.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.analysis_result import (
    Basis, EventType, EvidenceAnalysis, Finding, Hazard, PeopleRange, Severity, SeverityLevel, TimedObservation,
    UnusableReason,
)
from app.domain.evidence_reference import Modality
from app.domain.incident_summary import (
    MAX_ALERTS, MAX_EVIDENCES, AlertContext, EvidenceInput, IncidentSummary, SourcedStatement, SynthesisInput,
)
from app.schemas.analysis import provenance_json

# v2 agrega `keyPoints`, `hazardStates` y `resolvedHazards`; los campos de v1 no cambian de forma, así que un
# cliente de v1 sigue funcionando.
SUMMARY_SCHEMA_VERSION = "incident-summary.v2"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PeopleIn(_In):
    min: int = Field(ge=0)
    max: int = Field(ge=0)


class FindingIn(_In):
    text: str = Field(min_length=1, max_length=2000)
    basis: Basis


class SeverityIn(_In):
    level: SeverityLevel
    basis: list[str] = Field(max_length=20)


class TimedIn(_In):
    startSecond: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


class AnalysisIn(_In):
    summary: str = Field(min_length=1, max_length=2000)
    eventType: EventType
    people: PeopleIn | None = None
    hazards: list[Hazard] = Field(default_factory=list, max_length=20)
    observations: list[FindingIn] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    severity: SeverityIn
    limitations: list[str] = Field(default_factory=list, max_length=20)
    transcript: str | None = Field(default=None, max_length=10000)
    timeline: list[TimedIn] = Field(default_factory=list, max_length=20)
    # v2: un análisis de v1 no los trae y cuenta como una evidencia que sirve.
    usable: bool = True
    unusableReason: UnusableReason | None = None

    def to_domain(self) -> EvidenceAnalysis:
        usable = self.usable or self.unusableReason is None
        return EvidenceAnalysis(
            summary=self.summary,
            event_type=self.eventType,
            people=PeopleRange.of(self.people.min, self.people.max) if self.people else None,
            hazards=tuple(self.hazards),
            observations=tuple(Finding(o.text, o.basis) for o in self.observations),
            risks=tuple(self.risks),
            severity=Severity.assess(self.severity.level, self.severity.basis),
            limitations=tuple(self.limitations),
            transcript=self.transcript,
            timeline=tuple(TimedObservation(t.startSecond, t.text) for t in self.timeline),
            usable=usable,
            unusable_reason=None if usable else self.unusableReason,
        )


class AlertIn(_In):
    """Límites iguales a los que el backend acepta del ciudadano: una alerta válida allá no puede trabar el resumen."""

    alertId: int = Field(gt=0)
    reportedAt: datetime | None = None
    description: str | None = Field(default=None, max_length=2000)
    affectedCount: int | None = Field(default=None, ge=0, le=2**31 - 1)
    reporterIsPatient: bool | None = None


class EvidenceIn(_In):
    evidenceId: int = Field(gt=0)
    alertId: int = Field(gt=0)
    modality: Literal["image", "audio", "video"]
    receivedAt: datetime | None = None
    analysis: AnalysisIn


class SummaryRequest(_In):
    incidentId: int = Field(gt=0)
    alerts: list[AlertIn] = Field(min_length=1, max_length=MAX_ALERTS)
    evidences: list[EvidenceIn] = Field(default_factory=list, max_length=MAX_EVIDENCES)

    def to_domain(self) -> SynthesisInput:
        return SynthesisInput(
            incident_id=self.incidentId,
            alerts=tuple(
                AlertContext(a.alertId, a.reportedAt, a.description, a.affectedCount, a.reporterIsPatient)
                for a in self.alerts
            ),
            evidences=tuple(
                EvidenceInput(e.evidenceId, e.alertId, Modality(e.modality), e.receivedAt, e.analysis.to_domain())
                for e in self.evidences
            ),
        )


def _statement(statement: SourcedStatement, with_basis: bool = True) -> dict[str, Any]:
    data = {
        "text": statement.text,
        "evidenceIds": list(statement.evidence_ids),
        "alertIds": list(statement.alert_ids),
        "corroboratingAlerts": statement.corroborating_alerts,
    }
    if with_basis:
        data["basis"] = statement.basis
    return data


def summary_response(summary: IncidentSummary) -> dict[str, Any]:
    return {
        "incidentId": summary.incident_id,
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "summary": {
            "summary": summary.summary,
            "keyPoints": [{"kind": p.kind, "text": p.text} for p in summary.key_points],
            "eventType": summary.event_type,
            "people": {"min": summary.people.minimum, "max": summary.people.maximum} if summary.people else None,
            "hazards": list(summary.hazards),
            "hazardStates": [
                {
                    "type": state.hazard,
                    "status": state.status,
                    "lastReportedAt": state.last_reported_at.isoformat() if state.last_reported_at else None,
                }
                for state in summary.hazard_states
            ],
            "resolvedHazards": [
                {"type": item.hazard, "evidenceIds": list(item.evidence_ids), "alertIds": list(item.alert_ids)}
                for item in summary.resolved_hazards
            ],
            "findings": [_statement(f) for f in summary.findings],
            "risks": [_statement(r, with_basis=False) for r in summary.risks],
            "severity": {"level": summary.severity.level, "basis": list(summary.severity.basis)},
            "conflicts": [_statement(c, with_basis=False) for c in summary.conflicts],
            "limitations": list(summary.limitations),
        },
        "usedEvidenceIds": list(summary.used_evidence_ids),
        "usedAlertIds": list(summary.used_alert_ids),
        "provenance": provenance_json(summary.provenance),
    }
