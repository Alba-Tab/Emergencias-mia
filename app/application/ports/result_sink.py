"""Destino de los resultados individuales; el backend conserva su persistencia."""

from __future__ import annotations

from typing import Protocol

from app.domain.analysis_result import AnalysisResult


class ResultSink(Protocol):
    async def publish(self, result: AnalysisResult) -> None: ...
