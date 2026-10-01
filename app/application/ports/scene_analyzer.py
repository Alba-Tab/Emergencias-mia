"""Capacidad de interpretar una imagen, independiente del proveedor."""

from __future__ import annotations

from typing import Protocol

from app.domain.analysis_result import SceneAnalysis


class SceneAnalyzer(Protocol):
    async def analyze(self, image: bytes, mime_type: str) -> SceneAnalysis: ...
