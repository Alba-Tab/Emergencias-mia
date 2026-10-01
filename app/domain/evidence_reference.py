"""Datos mínimos para leer una evidencia ya autorizada por el backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: int
    alert_id: int
    incident_id: int
    bucket: str
    object_key: str
    mime_type: str
    checksum_sha256: str | None = None

    def __post_init__(self) -> None:
        if min(self.evidence_id, self.alert_id, self.incident_id) <= 0:
            raise ValueError("Los identificadores deben ser positivos")
        if not self.bucket or not self.object_key:
            raise ValueError("La evidencia necesita bucket y object_key")
        if self.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Tipo de imagen no admitido")
