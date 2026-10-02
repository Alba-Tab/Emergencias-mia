"""Datos mínimos para leer una evidencia ya autorizada por el backend."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Modality(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: int
    alert_id: int
    incident_id: int
    download_url: str
    mime_type: str
    checksum_sha256: str

    def __post_init__(self) -> None:
        if min(self.evidence_id, self.alert_id, self.incident_id) <= 0:
            raise ValueError("Los identificadores deben ser positivos")
        if not self.download_url:
            raise ValueError("La evidencia necesita download_url")
        if not self.mime_type or "/" not in self.mime_type:
            raise ValueError("mime_type inválido")
        if len(self.checksum_sha256) != 64:
            raise ValueError("checksum_sha256 debe ser SHA-256 hexadecimal")
