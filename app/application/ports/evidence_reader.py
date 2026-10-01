"""Capacidad de leer una evidencia desde almacenamiento privado."""

from __future__ import annotations

from typing import Protocol

from app.domain.evidence_reference import EvidenceReference


class EvidenceReader(Protocol):
    async def read(self, evidence: EvidenceReference) -> bytes: ...
