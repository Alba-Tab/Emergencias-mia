import unittest
from datetime import timezone
from hashlib import sha256

from app.application.use_cases.analyze_evidence import AnalyzeEvidence
from app.domain.analysis_result import SceneAnalysis
from app.domain.evidence_reference import EvidenceReference


class Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    async def read(self, evidence: EvidenceReference) -> bytes:
        self.calls += 1
        return self.content


class Analyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, image: bytes, mime_type: str) -> SceneAnalysis:
        self.calls += 1
        return SceneAnalysis(
            summary="Vehículo detenido en la vía",
            observations=("Un vehículo visible",),
            risks=("Tránsito cercano",),
            limitations=("No se puede confirmar el número de heridos",),
            provider="proveedor-prueba",
            model="modelo-prueba",
            prompt_version="image-v1",
        )


class Sink:
    def __init__(self) -> None:
        self.results = []

    async def publish(self, result) -> None:
        self.results.append(result)


class AnalyzeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.evidence = EvidenceReference(
            evidence_id=3,
            alert_id=2,
            incident_id=1,
            bucket="private-evidence",
            object_key="evidencias/1/2/3",
            mime_type="image/jpeg",
        )
        self.reader = Reader(b"\xff\xd8\xffimage-bytes")
        self.analyzer = Analyzer()
        self.sink = Sink()
        self.use_case = AnalyzeEvidence(self.reader, self.analyzer, self.sink)

    async def test_delivers_one_result_for_the_given_evidence(self) -> None:
        result = await self.use_case.execute("job-1", self.evidence)

        self.assertEqual(result.job_id, "job-1")
        self.assertEqual(result.evidence_id, 3)
        self.assertEqual(result.incident_id, 1)
        self.assertEqual(result.scene.provider, "proveedor-prueba")
        self.assertIsNotNone(result.analyzed_at.tzinfo)
        self.assertEqual(result.analyzed_at.utcoffset(), timezone.utc.utcoffset(None))
        self.assertEqual(self.reader.calls, 1)
        self.assertEqual(self.analyzer.calls, 1)
        self.assertEqual(self.sink.results, [result])

    async def test_rejects_empty_image_before_provider_and_callback(self) -> None:
        self.reader.content = b""

        with self.assertRaisesRegex(ValueError, "vacía"):
            await self.use_case.execute("job-1", self.evidence)

        self.assertEqual(self.analyzer.calls, 0)
        self.assertEqual(self.sink.results, [])

    async def test_rejects_image_over_limit_before_provider_and_callback(self) -> None:
        self.reader.content = b"12345"
        self.use_case = AnalyzeEvidence(self.reader, self.analyzer, self.sink, max_image_bytes=4)

        with self.assertRaisesRegex(ValueError, "tamaño"):
            await self.use_case.execute("job-1", self.evidence)

        self.assertEqual(self.analyzer.calls, 0)
        self.assertEqual(self.sink.results, [])

    async def test_rejects_missing_job_id_before_reading(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_id"):
            await self.use_case.execute(" ", self.evidence)

        self.assertEqual(self.reader.calls, 0)

    async def test_rejects_non_image_before_reading(self) -> None:
        evidence = EvidenceReference(3, 2, 1, "private-evidence", "key", "video/mp4")
        with self.assertRaisesRegex(ValueError, "Tipo de imagen"):
            await self.use_case.execute("job-1", evidence)
        self.assertEqual(self.reader.calls, 0)

    async def test_rejects_image_with_wrong_checksum(self) -> None:
        evidence = EvidenceReference(
            evidence_id=3,
            alert_id=2,
            incident_id=1,
            bucket="private-evidence",
            object_key="evidencias/1/2/3",
            mime_type="image/jpeg",
            checksum_sha256=sha256(b"other-image").hexdigest(),
        )

        with self.assertRaisesRegex(ValueError, "checksum"):
            await self.use_case.execute("job-1", evidence)

        self.assertEqual(self.analyzer.calls, 0)
        self.assertEqual(self.sink.results, [])

    async def test_rejects_mime_mismatch(self) -> None:
        self.reader.content = b"not-a-jpeg"
        with self.assertRaisesRegex(ValueError, "MIME"):
            await self.use_case.execute("job-1", self.evidence)
        self.assertEqual(self.analyzer.calls, 0)


class EvidenceReferenceTests(unittest.TestCase):
    def test_reference_is_media_agnostic(self) -> None:
        reference = EvidenceReference(1, 1, 1, "bucket", "key", "video/mp4")
        self.assertEqual(reference.mime_type, "video/mp4")

    def test_rejects_invalid_mime(self) -> None:
        with self.assertRaisesRegex(ValueError, "mime_type"):
            EvidenceReference(1, 1, 1, "bucket", "key", "invalid")
