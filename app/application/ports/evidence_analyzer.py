"""Capacidad de analizar una modalidad de evidencia."""

from __future__ import annotations

from typing import Protocol

from app.domain.analysis_result import AnalysisResult
from app.domain.evidence_reference import EvidenceReference


class EvidenceAnalyzer(Protocol):
    async def execute(self, job_id: str, evidence: EvidenceReference) -> AnalysisResult: ...
