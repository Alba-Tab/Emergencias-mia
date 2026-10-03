import unittest
from datetime import datetime, timezone
from hashlib import sha256

import httpx

from app.adapters.inbound.http.dependencies import authorize
from app.adapters.outbound.providers.prueba_model import LIMITATION, PruebaModel
from app.application.ports.media_preparer import Frame, PreparedVideo
from app.application.prompts import load_prompt
from app.application.structured_output import AudioOutput, EvidenceOutput, VideoOutput, parse_output
from app.application.use_cases.synthesize_summary import SynthesizeIncidentSummary
from app.core.composition import build_services
from app.core.config import Settings
from app.domain.analysis_result import Finding, PeopleRange
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference, Modality
from app.domain.incident_summary import AlertContext, EvidenceInput, SynthesisInput
from tests.fixtures import synthetic_bmff, synthetic_png
from tests.unit.test_use_cases import FakePreparer, FakeReader, analysis

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def reference(data, mime):
    return EvidenceReference(1, 2, 3, "https://objects.example/k?sig=x", mime, sha256(data).hexdigest())


class CompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prueba_needs_no_provider_key(self):
        async with httpx.AsyncClient() as client:
            self.assertIsNone(build_services(settings(), client))
            services = build_services(settings(provider="prueba"), client)
        self.assertIsNotNone(services)
        self.assertIsInstance(services.synthesize.model, PruebaModel)

    def test_prueba_still_requires_service_token(self):
        with self.assertRaises(AiError) as caught:
            authorize("Bearer x", settings(provider="prueba"))
        self.assertEqual(caught.exception.code, "service_not_configured")


class PruebaAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def analyze(self, data, mime, preparer=None):
        async with httpx.AsyncClient() as client:
            services = build_services(settings(provider="prueba"), client)
        services.analyze.reader = FakeReader(data)
        # Sin ffmpeg: la duración sale de la cabecera, como en un equipo que no lo tiene instalado.
        services.analyze.preparer = preparer
        return await services.analyze.execute("job", reference(data, mime))

    async def test_outputs_per_modality(self):
        image = await self.analyze(synthetic_png(), "image/png")
        self.assertEqual((image.provenance.provider, image.provenance.prompt_version), ("prueba", "image-v2"))
        self.assertIn("smoke", image.analysis.hazards)
        self.assertTrue(image.analysis.observations)
        self.assertIn(LIMITATION, image.analysis.limitations)

        audio = await self.analyze(synthetic_bmff(20, b"M4A "), "audio/mp4")
        self.assertIn("[nombre]", audio.analysis.transcript)
        self.assertIn("[número]", audio.analysis.transcript)

        video = await self.analyze(synthetic_bmff(20), "video/mp4")
        self.assertEqual([t.start_second for t in video.analysis.timeline], [0, 10])
        self.assertEqual(video.provenance.provider, "prueba")

    async def test_video_frames_use_only_the_received_seconds(self):
        preparer = FakePreparer(PreparedVideo(4.0, (Frame(0.0, b"a"), Frame(2.5, b"b"), Frame(3.96, b"c")), b"m4a"))
        video = await self.analyze(synthetic_bmff(4), "video/mp4", preparer)
        self.assertEqual([t.start_second for t in video.analysis.timeline], [0.0, 2.5, 3.96])
        self.assertEqual(video.provenance.prompt_version, "video-v2")
        self.assertTrue(video.analysis.transcript)

        silent = FakePreparer(PreparedVideo(4.0, (Frame(0.0, b"a"), Frame(3.9, b"b")), None))
        video = await self.analyze(synthetic_bmff(4), "video/mp4", silent)
        self.assertIsNone(video.analysis.transcript)

    async def test_is_deterministic_and_schema_valid(self):
        model = PruebaModel()
        for name, output in (("image_evidence", EvidenceOutput), ("audio_evidence", AudioOutput),
                             ("video_evidence", VideoOutput)):
            first = await model.generate(instructions="", text="", media=None, schema_name=name, schema={})
            second = await model.generate(instructions="", text="", media=None, schema_name=name, schema={})
            self.assertEqual(first, second)
            parse_output(output, first.content)

    async def test_media_validation_still_runs(self):
        png = synthetic_png()
        async with httpx.AsyncClient() as client:
            services = build_services(settings(provider="prueba"), client)
        services.analyze.reader = FakeReader(png)
        services.analyze.preparer = None
        bad = EvidenceReference(1, 2, 3, "https://objects.example/k", "image/png", "0" * 64)
        with self.assertRaises(AiError) as caught:
            await services.analyze.execute("job", bad)
        self.assertEqual(caught.exception.code, "checksum_mismatch")
        services.analyze.reader = FakeReader(synthetic_bmff(61))
        with self.assertRaises(AiError) as caught:
            await services.analyze.execute("job", reference(synthetic_bmff(61), "video/mp4"))
        self.assertEqual(caught.exception.code, "duration_exceeded")


class PruebaSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cites_only_received_sources(self):
        source = SynthesisInput(
            5,
            (AlertContext(1, NOW, None, None, None), AlertContext(2, NOW, "Hay humo", 4, False)),
            (EvidenceInput(10, 1, Modality.IMAGE, NOW, analysis()),
             EvidenceInput(11, 2, Modality.VIDEO, NOW, analysis(
                 event_type="fire", people=PeopleRange(2, 3), observations=(Finding("Humo.", "observed"),)))),
        )
        with self.assertNoLogs("app", level="WARNING"):
            summary = await SynthesizeIncidentSummary(PruebaModel(), load_prompt("summary_v1"), lambda: NOW).execute(
                source)
        cited = {(f.evidence_ids, f.alert_ids) for f in summary.findings}
        self.assertEqual(cited, {((10,), ()), ((11,), ()), ((), (2,))})
        self.assertEqual(len(summary.conflicts), 1)
        self.assertEqual(summary.people, PeopleRange(4, 4))
        self.assertEqual(summary.severity.level, "moderate")
        self.assertEqual(summary.provenance.provider, "prueba")
        self.assertEqual(summary.used_alert_ids, (1, 2))
