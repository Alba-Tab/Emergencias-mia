"""Capacidad de generar una respuesta estructurada a partir de texto y, opcionalmente, un medio.

Cambiar de proveedor (OpenRouter, Gemini directo o un modelo local) solo requiere otro adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.evidence_reference import Modality


@dataclass(frozen=True, slots=True)
class MediaPart:
    modality: Modality
    mime_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: dict[str, Any]
    provider: str
    model: str


class MultimodalModel(Protocol):
    async def generate(
        self, *, instructions: str, text: str, media: MediaPart | None, schema_name: str, schema: dict[str, Any],
    ) -> ModelReply: ...
