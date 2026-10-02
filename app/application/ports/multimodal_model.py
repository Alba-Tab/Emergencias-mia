"""Capacidad de generar una respuesta estructurada a partir de texto y, opcionalmente, medios.

Cambiar de proveedor (OpenRouter, Gemini directo o un modelo local) solo requiere otro adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any, Protocol

from app.domain.evidence_reference import Modality


@dataclass(frozen=True, slots=True)
class MediaPart:
    modality: Modality
    mime_type: str
    data: bytes
    second: float | None = None  # fotograma de un video: segundo exacto en que aparece


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: dict[str, Any]
    provider: str
    model: str


class MultimodalModel(Protocol):
    async def generate(
        self,
        *,
        instructions: str,
        text: str,
        media: MediaPart | Sequence[MediaPart] | None,
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelReply: ...


def media_parts(media: MediaPart | Sequence[MediaPart] | None) -> tuple[MediaPart, ...]:
    """Normaliza el argumento `media` a una tupla en el orden en que se envía."""
    if media is None:
        return ()
    if isinstance(media, MediaPart):
        return (media,)
    return tuple(media)
