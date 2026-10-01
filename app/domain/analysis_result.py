"""Resultado individual de IA, sin entidades ni tipos del backend Spring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SceneAnalysis:
    summary: str
    observations: tuple[str, ...]
    risks: tuple[str, ...]
    limitations: tuple[str, ...]
    provider: str
    model: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    job_id: str
    evidence_id: int
    alert_id: int
    incident_id: int
    scene: SceneAnalysis
    analyzed_at: datetime
