"""Reglas de formato, tamaño y duración por modalidad, sin dependencias externas.

La duración se mide en contenedores ISO BMFF (MP4, M4A, MOV) y WAV, que son los que graban
las apps. Para el resto de formatos admitidos solo se acota el tamaño.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256

from app.domain.errors import AiError
from app.domain.evidence_reference import Modality


def _is_bmff(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _is_mp3(data: bytes) -> bool:
    return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


SIGNATURES: Mapping[str, Callable[[bytes], bool]] = {
    "image/jpeg": lambda d: d.startswith(b"\xff\xd8\xff"),
    "image/png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda d: d.startswith(b"RIFF") and d[8:12] == b"WEBP",
    "audio/mp4": _is_bmff,
    "audio/m4a": _is_bmff,
    "audio/x-m4a": _is_bmff,
    "audio/mpeg": _is_mp3,
    "audio/aac": lambda d: len(d) >= 2 and d[0] == 0xFF and d[1] & 0xF6 == 0xF0,
    "audio/wav": lambda d: d.startswith(b"RIFF") and d[8:12] == b"WAVE",
    "audio/x-wav": lambda d: d.startswith(b"RIFF") and d[8:12] == b"WAVE",
    "audio/ogg": lambda d: d.startswith(b"OggS"),
    "video/mp4": _is_bmff,
    "video/quicktime": _is_bmff,
    "video/webm": lambda d: d.startswith(b"\x1a\x45\xdf\xa3"),
}


def _boxes(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        header = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            return
        yield kind, offset + header, offset + size
        offset += size


def bmff_duration_seconds(data: bytes) -> float | None:
    """Duración declarada en `moov/mvhd`; `None` si el contenedor no la trae."""
    for kind, start, end in _boxes(data, 0, len(data)):
        if kind != b"moov":
            continue
        for inner, body, body_end in _boxes(data, start, end):
            if inner != b"mvhd" or body_end - body < 20:
                continue
            version = data[body]
            if version == 1 and body_end - body >= 32:
                timescale, duration = struct.unpack(">IQ", data[body + 20:body + 32])
            else:
                timescale, duration = struct.unpack(">II", data[body + 12:body + 20])
            return duration / timescale if timescale else None
    return None


def wav_duration_seconds(data: bytes) -> float | None:
    byte_rate = None
    offset = 12
    while offset + 8 <= len(data):
        kind, size = struct.unpack("<4sI", data[offset:offset + 8])
        if kind == b"fmt " and size >= 12:
            byte_rate = struct.unpack("<I", data[offset + 16:offset + 20])[0]
        elif kind == b"data":
            return size / byte_rate if byte_rate else None
        offset += 8 + size + (size & 1)
    return None


def duration_seconds(data: bytes, mime_type: str) -> float | None:
    if SIGNATURES.get(mime_type) is _is_bmff:
        return bmff_duration_seconds(data)
    if mime_type in {"audio/wav", "audio/x-wav"}:
        return wav_duration_seconds(data)
    return None


@dataclass(frozen=True, slots=True)
class MediaPolicy:
    modality: Modality
    mime_types: frozenset[str]
    max_bytes: int
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or (self.max_seconds is not None and self.max_seconds <= 0):
            raise ValueError("Los límites deben ser positivos")
        if not self.mime_types <= SIGNATURES.keys():
            raise ValueError("MIME sin firma conocida")

    def validate(self, data: bytes, mime_type: str, checksum_sha256: str) -> None:
        """Rechaza el archivo antes de enviarlo al proveedor; el orden va de lo barato a lo caro."""
        if mime_type not in self.mime_types:
            raise AiError("unsupported_media_type")
        if not data:
            raise AiError("evidence_empty")
        if len(data) > self.max_bytes:
            raise AiError("evidence_too_large")
        if not SIGNATURES[mime_type](data):
            raise AiError("mime_mismatch")
        if sha256(data).hexdigest() != checksum_sha256.lower():
            raise AiError("checksum_mismatch")
        if self.max_seconds is None:
            return
        seconds = duration_seconds(data, mime_type)
        if seconds is None and SIGNATURES[mime_type] is _is_bmff:
            raise AiError("unreadable_media")
        if seconds is not None and seconds > self.max_seconds:
            raise AiError("duration_exceeded")


IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
AUDIO_MIME_TYPES = frozenset({
    "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/mpeg", "audio/aac", "audio/wav", "audio/x-wav", "audio/ogg",
})
VIDEO_MIME_TYPES = frozenset({"video/mp4", "video/quicktime", "video/webm"})


def modality_of(mime_type: str) -> Modality:
    for modality, mime_types in (
        (Modality.IMAGE, IMAGE_MIME_TYPES), (Modality.AUDIO, AUDIO_MIME_TYPES), (Modality.VIDEO, VIDEO_MIME_TYPES),
    ):
        if mime_type in mime_types:
            return modality
    raise AiError("unsupported_media_type")
