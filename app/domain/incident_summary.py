"""Síntesis del incidente a partir de resultados ya analizados y datos de las alertas."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from app.domain.analysis_result import (
    BASES, EVENT_TYPES, HAZARDS, EvidenceAnalysis, PeopleRange, Provenance, Severity, clip,
)
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality

MAX_ALERTS = 50
MAX_EVIDENCES = 50

# Puntos clave para la tripulación: el orden de la lista es el orden en que se muestran y se leen.
KeyPointKind = Literal["what", "people", "hazard", "critical"]
KEY_POINT_KINDS: tuple[str, ...] = get_args(KeyPointKind)
MAX_KEY_POINTS = 4
# El prompt pide 90 caracteres; el recorte deja margen para no partir una frase casi completa.
MAX_KEY_POINT_TEXT = 120


@dataclass(frozen=True, slots=True)
class KeyPoint:
    """Una frase corta, en estilo radio, para leer en pantalla o en voz alta."""

    kind: str
    text: str

    def __post_init__(self) -> None:
        if self.kind not in KEY_POINT_KINDS:
            raise ValueError("kind desconocido")
        if not self.text.strip():
            raise ValueError("text es obligatorio")


def key_points(items: Iterable[tuple[str, str]], fallback: str) -> tuple[KeyPoint, ...]:
    """Orden fijo por tipo, un solo `what` y como máximo cuatro.

    Sin ninguna frase utilizable, el resumen general hace de `what`: la tarjeta nunca queda vacía.
    """
    kept: list[KeyPoint] = []
    for kind, text in items:
        text = clip(text, MAX_KEY_POINT_TEXT)
        if not text or (kind == "what" and any(point.kind == "what" for point in kept)):
            continue
        kept.append(KeyPoint(kind, text))
    kept.sort(key=lambda point: KEY_POINT_KINDS.index(point.kind))
    return tuple(kept[:MAX_KEY_POINTS]) or (KeyPoint("what", clip(fallback, MAX_KEY_POINT_TEXT)),)


HazardStatus = Literal["active", "unconfirmed"]
HAZARD_STATUSES: tuple[str, ...] = get_args(HazardStatus)


@dataclass(frozen=True, slots=True)
class HazardState:
    """`unconfirmed`: alguna evidencia lo mencionó y ninguna fuente dijo que terminó, pero el modelo lo omitió."""

    hazard: str
    status: str
    last_reported_at: datetime | None

    def __post_init__(self) -> None:
        if self.hazard not in HAZARDS or self.status not in HAZARD_STATUSES:
            raise ValueError("vocabulario desconocido")


@dataclass(frozen=True, slots=True)
class ResolvedHazard:
    """Un peligro que terminó, con las fuentes que lo dicen."""

    hazard: str
    evidence_ids: tuple[int, ...]
    alert_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.hazard not in HAZARDS:
            raise ValueError("vocabulario desconocido")


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

    def valid_citations(self, evidence_ids, alert_ids) -> tuple[set[int], set[int], int]:
        """Las citas a fuentes recibidas y cuántas se descartaron.

        Una alerta sin texto no aporta nada que citar, así que también se descarta.
        """
        cited_evidences, cited_alerts = set(evidence_ids), set(alert_ids)
        valid_evidences = cited_evidences & {e.evidence_id for e in self.evidences}
        valid_alerts = cited_alerts & {a.alert_id for a in self.alerts if a.has_text}
        dropped = len(cited_evidences - valid_evidences) + len(cited_alerts - valid_alerts)
        return valid_evidences, valid_alerts, dropped

    def reported_hazards(self) -> dict[str, datetime | None]:
        """Cada peligro que mencionó alguna evidencia, con la hora de la mención más reciente que se conoce."""
        latest: dict[str, datetime | None] = {}
        for evidence in self.evidences:
            when = evidence.received_at
            for hazard in evidence.analysis.hazards:
                current = latest.get(hazard)
                if hazard not in latest or (when is not None and (current is None or when > current)):
                    latest[hazard] = when
        return latest

    def hazard_states(
        self, active: Iterable[str], resolved: Iterable[tuple[str, Iterable[int], Iterable[int]]],
    ) -> tuple[tuple[HazardState, ...], tuple[ResolvedHazard, ...]]:
        """Un peligro que mencionó alguna evidencia no desaparece del resumen sin motivo.

        Sale de la lista solo si el modelo lo da por terminado citando una fuente recibida. Si lo omitió sin
        eso, vuelve como `unconfirmed` con la hora de su última mención. Si el modelo lo da a la vez por activo
        y por terminado, queda activo: ante la duda, el peligro se conserva.
        """
        reported = self.reported_hazards()
        active = tuple(dict.fromkeys(active))
        ended: list[ResolvedHazard] = []
        for hazard, evidence_ids, alert_ids in resolved:
            if hazard in active or any(item.hazard == hazard for item in ended):
                continue
            evidences, alerts, _ = self.valid_citations(evidence_ids, alert_ids)
            if evidences or alerts:
                ended.append(ResolvedHazard(hazard, tuple(sorted(evidences)), tuple(sorted(alerts))))
        ended_hazards = {item.hazard for item in ended}
        states = [HazardState(hazard, "active", reported.get(hazard)) for hazard in active]
        states += [
            HazardState(hazard, "unconfirmed", when)
            for hazard, when in reported.items()
            if hazard not in active and hazard not in ended_hazards
        ]
        return tuple(states), tuple(ended)

    def sourced(self, text: str, basis: str, evidence_ids, alert_ids) -> tuple[SourcedStatement | None, int]:
        """Conserva solo las citas a fuentes recibidas y calcula la corroboración con ellas.

        Devuelve la afirmación (o `None` si no le queda ninguna fuente válida) y cuántas citas se
        descartaron.
        """
        valid_evidences, valid_alerts, dropped = self.valid_citations(evidence_ids, alert_ids)
        if not valid_evidences and not valid_alerts:
            return None, dropped
        alerts = valid_alerts | {self.alert_of(evidence_id) for evidence_id in valid_evidences}
        statement = SourcedStatement(
            clip(text), basis, tuple(sorted(valid_evidences)), tuple(sorted(valid_alerts)), len(alerts),
        )
        return statement, dropped


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
    key_points: tuple[KeyPoint, ...]
    event_type: str
    people: PeopleRange | None
    hazard_states: tuple[HazardState, ...]
    findings: tuple[SourcedStatement, ...]
    risks: tuple[SourcedStatement, ...]
    severity: Severity
    conflicts: tuple[SourcedStatement, ...]
    limitations: tuple[str, ...]
    used_evidence_ids: tuple[int, ...]
    used_alert_ids: tuple[int, ...]
    provenance: Provenance
    resolved_hazards: tuple[ResolvedHazard, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("summary es obligatorio")
        if not self.key_points or len(self.key_points) > MAX_KEY_POINTS:
            raise ValueError("key_points fuera de rango")
        if self.event_type not in EVENT_TYPES:
            raise ValueError("vocabulario desconocido")

    @property
    def hazards(self) -> tuple[str, ...]:
        """Los peligros que no terminaron, activos primero: la misma lista de textos que en v1."""
        return tuple(state.hazard for state in self.hazard_states)


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
        key_points=key_points((), analysis.summary),
        event_type=analysis.event_type,
        people=analysis.people,
        hazard_states=tuple(HazardState(hazard, "active", evidence.received_at) for hazard in analysis.hazards),
        findings=tuple(SourcedStatement(f.text, f.basis, ids, (), 1) for f in analysis.observations),
        risks=tuple(SourcedStatement(risk, "inferred", ids, (), 1) for risk in analysis.risks),
        severity=analysis.severity,
        conflicts=(),
        limitations=analysis.limitations,
        used_evidence_ids=used_evidences,
        used_alert_ids=used_alerts,
        provenance=Provenance(None, None, None, generated_at, "single_evidence"),
    )
