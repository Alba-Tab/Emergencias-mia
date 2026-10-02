"""Resultado individual de una evidencia, sin entidades ni tipos del backend Spring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from app.domain.evidence_reference import Modality

EventType = Literal[
    "traffic_accident", "fire", "explosion", "medical_emergency", "fall_or_injury", "violence",
    "drowning_or_flood", "structural_collapse", "hazardous_material", "other", "undetermined",
]
Hazard = Literal[
    "fire", "smoke", "traffic", "electrical", "gas_or_chemical", "structural_instability",
    "water", "weapon_or_violence", "crowd", "height", "other",
]
SeverityLevel = Literal["low", "moderate", "high", "undetermined"]
Basis = Literal["observed", "inferred"]

EVENT_TYPES: tuple[str, ...] = get_args(EventType)
HAZARDS: tuple[str, ...] = get_args(Hazard)
SEVERITY_LEVELS: tuple[str, ...] = get_args(SeverityLevel)
BASES: tuple[str, ...] = get_args(Basis)

MAX_ITEMS = 12
MAX_TEXT = 600
MAX_TRANSCRIPT = 8000


def clip(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clip_all(items, limit: int = MAX_TEXT) -> tuple[str, ...]:
    return tuple(clip(item, limit) for item in items if item and item.strip())[:MAX_ITEMS]


@dataclass(frozen=True, slots=True)
class PeopleRange:
    minimum: int
    maximum: int

    @classmethod
    def of(cls, minimum: int | None, maximum: int | None) -> PeopleRange | None:
        """Un rango incompleto o incoherente se considera desconocido."""
        if minimum is None or maximum is None or minimum < 0 or maximum < minimum:
            return None
        return cls(minimum, maximum)


@dataclass(frozen=True, slots=True)
class Severity:
    """Orientación preliminar; nunca es triaje ni decisión de despacho."""

    level: str
    basis: tuple[str, ...]

    @classmethod
    def assess(cls, level: str, basis) -> Severity:
        basis = clip_all(basis)
        if level not in SEVERITY_LEVELS or level == "undetermined" or not basis:
            # Una gravedad sin observaciones que la justifiquen se degrada a indeterminada.
            return cls("undetermined", basis if level == "undetermined" else ())
        return cls(level, basis)


@dataclass(frozen=True, slots=True)
class Finding:
    text: str
    basis: str


@dataclass(frozen=True, slots=True)
class TimedObservation:
    start_second: float
    text: str


@dataclass(frozen=True, slots=True)
class EvidenceAnalysis:
    """Contenido común a toda modalidad; `transcript` y `timeline` solo aplican a audio/video."""

    summary: str
    event_type: str
    people: PeopleRange | None
    hazards: tuple[str, ...]
    observations: tuple[Finding, ...]
    risks: tuple[str, ...]
    severity: Severity
    limitations: tuple[str, ...]
    transcript: str | None = None
    timeline: tuple[TimedObservation, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("summary es obligatorio")
        if self.event_type not in EVENT_TYPES:
            raise ValueError("event_type desconocido")
        if any(hazard not in HAZARDS for hazard in self.hazards):
            raise ValueError("hazard desconocido")
        if any(finding.basis not in BASES for finding in self.observations):
            raise ValueError("basis desconocido")


@dataclass(frozen=True, slots=True)
class Provenance:
    provider: str | None
    model: str | None
    prompt_version: str | None
    generated_at: datetime
    method: str  # "model" o "single_evidence"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    job_id: str
    evidence_id: int
    alert_id: int
    incident_id: int
    modality: Modality
    analysis: EvidenceAnalysis
    provenance: Provenance
