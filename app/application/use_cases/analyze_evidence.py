"""Coordina la lectura, análisis y entrega de una sola imagen."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from app.application.ports.evidence_reader import EvidenceReader
from app.application.ports.result_sink import ResultSink
from app.application.ports.scene_analyzer import SceneAnalyzer
from app.domain.analysis_result import AnalysisResult
from app.domain.evidence_reference import EvidenceReference


class AnalyzeEvidence:
    def __init__(
        self,
        reader: EvidenceReader,
        analyzer: SceneAnalyzer,
        sink: ResultSink,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes debe ser positivo")
        self.reader = reader
        self.analyzer = analyzer
        self.sink = sink
        self.max_image_bytes = max_image_bytes

    async def execute(self, job_id: str, evidence: EvidenceReference) -> AnalysisResult:
        if not job_id.strip():
            raise ValueError("job_id es obligatorio")

        image = await self.reader.read(evidence)
        if not image:
            raise ValueError("La imagen está vacía")
        if len(image) > self.max_image_bytes:
            raise ValueError("La imagen supera el tamaño permitido")
        if evidence.checksum_sha256 and sha256(image).hexdigest() != evidence.checksum_sha256.lower():
            raise ValueError("El checksum de la imagen no coincide")

        scene = await self.analyzer.analyze(image, evidence.mime_type)
        result = AnalysisResult(
            job_id=job_id,
            evidence_id=evidence.evidence_id,
            incident_id=evidence.incident_id,
            scene=scene,
            analyzed_at=datetime.now(timezone.utc),
        )
        await self.sink.publish(result)
        return result
