"""Selecciona el análisis de la modalidad y entrega el resultado individual."""

from __future__ import annotations

from collections.abc import Mapping

from app.application.ports.evidence_analyzer import EvidenceAnalyzer
from app.application.ports.result_sink import ResultSink
from app.domain.analysis_result import AnalysisResult
from app.domain.evidence_reference import EvidenceReference


class UnsupportedModalityError(ValueError):
    """No existe todavía un analizador registrado para esta modalidad."""


class AnalyzeEvidence:
    def __init__(self, analyzers: Mapping[str, EvidenceAnalyzer], sink: ResultSink) -> None:
        self.analyzers = dict(analyzers)
        self.sink = sink

    async def execute(self, job_id: str, evidence: EvidenceReference) -> AnalysisResult:
        if not job_id.strip():
            raise ValueError("job_id es obligatorio")
        modality = evidence.mime_type.partition("/")[0]
        analyzer = self.analyzers.get(modality)
        if analyzer is None:
            raise UnsupportedModalityError(f"Modalidad no implementada: {modality}")

        result = await analyzer.execute(job_id, evidence)
        await self.sink.publish(result)
        return result
