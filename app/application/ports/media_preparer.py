"""Capacidad de medir un audio o video y de reducir un video a fotogramas y audio.

El análisis por fotogramas manda al modelo unas pocas imágenes con su segundo exacto y la pista
de audio, en lugar del video completo: cuesta menos y la línea de tiempo queda anclada a datos reales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MediaPreparationError(Exception):
    """La herramienta no pudo preparar el medio (no está instalada, tardó demasiado o falló).

    No dice nada del archivo: un archivo ilegible se informa con `AiError("unreadable_media")`.
    """


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration: float | None
    has_video: bool
    has_audio: bool
    start_time: float = 0.0  # primer instante del contenedor; los segundos de los fotogramas se miden desde aquí


@dataclass(frozen=True, slots=True)
class Frame:
    second: float
    data: bytes  # JPEG


@dataclass(frozen=True, slots=True)
class PreparedVideo:
    duration: float
    frames: tuple[Frame, ...]
    audio: bytes | None  # M4A (AAC mono); `None` si el video no tiene audio
    audio_mime_type: str = "audio/mp4"


class MediaPreparer(Protocol):
    async def probe(self, data: bytes, mime_type: str) -> MediaInfo: ...

    async def prepare_video(
        self, data: bytes, mime_type: str, info: MediaInfo, max_frames: int, scene_threshold: float,
    ) -> PreparedVideo: ...
