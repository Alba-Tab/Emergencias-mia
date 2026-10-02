import unittest
from datetime import datetime, timezone
from hashlib import sha256

from app.application.pipelines.media import AUDIO_MIME_TYPES, IMAGE_MIME_TYPES, VIDEO_MIME_TYPES, MediaPolicy
from app.application.ports.multimodal_model import ModelReply
from app.application.prompts import load_prompt
from app.application.structured_output import AudioOutput, EvidenceOutput, VideoOutput, json_schema
from app.application.use_cases.analyze_evidence import AnalyzeEvidence, ModalityProfile
from app.application.use_cases.synthesize_summary import SynthesizeIncidentSummary
from app.domain.analysis_result import EvidenceAnalysis, Finding, PeopleRange, Severity
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference, Modality
from app.domain.incident_summary import AlertContext, EvidenceInput, SynthesisInput
from tests.fixtures import evidence_output, synthetic_bmff, synthetic_png

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(reply, "fake", "fake-model")


class FakeReader:
    def __init__(self, data):
        self.data = data
        self.limits = []

    async def read(self, evidence, max_bytes):
        self.limits.append(max_bytes)
        return self.data


def reference(data, mime="image/png"):
    return EvidenceReference(1, 2, 3, "https://objects.example/k?sig=x", mime, sha256(data).hexdigest())


def build(data, model):
    profiles = {
        Modality.IMAGE: ModalityProfile(MediaPolicy(Modality.IMAGE, IMAGE_MIME_TYPES, 1024),
                                        load_prompt("image_v2"), EvidenceOutput, model),
        Modality.AUDIO: ModalityProfile(MediaPolicy(Modality.AUDIO, AUDIO_MIME_TYPES, 4096, 120),
                                        load_prompt("audio_v1"), AudioOutput, model),
        Modality.VIDEO: ModalityProfile(MediaPolicy(Modality.VIDEO, VIDEO_MIME_TYPES, 4096, 60),
                                        load_prompt("video_v1"), VideoOutput, model),
    }
    return AnalyzeEvidence(FakeReader(data), profiles, clock=lambda: NOW)


class AnalyzeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_result_and_provenance(self):
        model = FakeModel(evidence_output())
        use_case = build(synthetic_png(), model)
        result = await use_case.execute("job-1", reference(synthetic_png()))
        self.assertIs(result.modality, Modality.IMAGE)
        self.assertEqual(result.analysis.people, PeopleRange(1, 2))
        self.assertEqual(result.analysis.severity.level, "moderate")
        self.assertEqual(result.analysis.observations[1].basis, "inferred")
        self.assertIsNone(result.analysis.transcript)
        self.assertEqual((result.provenance.prompt_version, result.provenance.model, result.provenance.method),
                         ("image-v2", "fake-model", "model"))
        self.assertEqual(use_case.reader.limits, [1024])
        call = model.calls[0]
        self.assertEqual(call["schema_name"], "image_evidence")
        self.assertEqual(call["media"].data, synthetic_png())
        self.assertNotIn("$ref", str(call["schema"]))

    async def test_audio_and_video_outputs(self):
        m4a = synthetic_bmff(20, b"M4A ")
        audio = await build(m4a, FakeModel(evidence_output(transcript="Hay [nombre] herido"))).execute(
            "job-2", reference(m4a, "audio/mp4"))
        self.assertEqual(audio.analysis.transcript, "Hay [nombre] herido")

        mp4 = synthetic_bmff(20)
        timeline = [{"startSecond": 9, "text": "Llega humo"}, {"startSecond": 1, "text": "Fuego en cocina"}]
        video = await build(mp4, FakeModel(evidence_output(transcript="", timeline=timeline))).execute(
            "job-3", reference(mp4, "video/mp4"))
        self.assertEqual([t.start_second for t in video.analysis.timeline], [1, 9])

    async def test_severity_without_basis_is_undetermined_and_bad_people_unknown(self):
        model = FakeModel(evidence_output(severity="high", severityBasis=[], peopleMin=3, peopleMax=1))
        result = await build(synthetic_png(), model).execute("job", reference(synthetic_png()))
        self.assertEqual(result.analysis.severity, Severity("undetermined", ()))
        self.assertIsNone(result.analysis.people)

    async def test_invalid_media_never_reaches_model(self):
        model = FakeModel()
        with self.assertRaises(AiError) as caught:
            await build(synthetic_png(), model).execute("job", reference(synthetic_png(), "image/jpeg"))
        self.assertEqual(caught.exception.code, "mime_mismatch")
        with self.assertRaises(AiError) as caught:
            await build(b"%PDF", model).execute("job", reference(b"%PDF", "application/pdf"))
        self.assertEqual(caught.exception.code, "unsupported_media_type")
        self.assertEqual(model.calls, [])

    async def test_invalid_model_output_is_retryable(self):
        model = FakeModel(evidence_output(eventType="alien_invasion"))
        with self.assertRaises(AiError) as caught:
            await build(synthetic_png(), model).execute("job", reference(synthetic_png()))
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("invalid_model_output", True))


def analysis(summary="Choque entre dos autos.", **overrides):
    data = dict(
        summary=summary, event_type="traffic_accident", people=PeopleRange(1, 2), hazards=("traffic",),
        observations=(Finding("Dos autos dañados.", "observed"),), risks=("Tráfico cercano.",),
        severity=Severity.assess("moderate", ["Daños visibles."]), limitations=("Interior no visible.",),
    )
    data.update(overrides)
    return EvidenceAnalysis(**data)


def summary_output(**overrides):
    data = {
        "summary": "Choque de dos autos con una persona atrapada.",
        "eventType": "traffic_accident", "peopleMin": 1, "peopleMax": 3, "hazards": ["traffic"],
        "findings": [{"text": "Dos autos dañados.", "basis": "observed", "evidenceIds": [10, 11], "alertIds": [2]}],
        "risks": [{"text": "Tráfico cercano.", "evidenceIds": [10], "alertIds": []}],
        "severity": "moderate", "severityBasis": ["Persona atrapada según la alerta 2."],
        "conflicts": [{"text": "La alerta 2 dice 3 personas; la imagen muestra 1.", "evidenceIds": [10], "alertIds": [2]}],
        "limitations": ["Interior no visible."],
    }
    data.update(overrides)
    return data


class SynthesizeTests(unittest.IsolatedAsyncioTestCase):
    def source(self, with_text=True, evidences=2):
        alerts = (
            AlertContext(1, NOW, None, None, None),
            AlertContext(2, NOW, "Hay alguien atrapado" if with_text else None, 3 if with_text else None, False),
        )
        items = (EvidenceInput(10, 1, Modality.IMAGE, NOW, analysis()),
                 EvidenceInput(11, 2, Modality.AUDIO, NOW, analysis(transcript="ayuda")))
        return SynthesisInput(5, alerts, items[:evidences])

    async def test_single_evidence_skips_model(self):
        model = FakeModel()
        summary = await SynthesizeIncidentSummary(model, load_prompt("summary_v1"), lambda: NOW).execute(
            self.source(with_text=False, evidences=1))
        self.assertEqual(model.calls, [])
        self.assertEqual(summary.provenance.method, "single_evidence")
        self.assertEqual(summary.findings[0].evidence_ids, (10,))
        self.assertEqual(summary.used_evidence_ids, (10,))

    async def test_model_synthesis_computes_corroboration(self):
        model = FakeModel(summary_output())
        summary = await SynthesizeIncidentSummary(model, load_prompt("summary_v1"), lambda: NOW).execute(self.source())
        self.assertEqual(summary.findings[0].corroborating_alerts, 2)
        self.assertEqual(summary.risks[0].corroborating_alerts, 1)
        self.assertEqual(summary.used_evidence_ids, (10, 11))
        self.assertEqual(summary.used_alert_ids, (1, 2))
        self.assertEqual(summary.provenance.prompt_version, "summary-v1")
        text = model.calls[0]["text"]
        self.assertIn("<fuentes>", text)
        self.assertIn("Hay alguien atrapado", text)
        self.assertIsNone(model.calls[0]["media"])

    async def test_rejects_invented_or_missing_sources(self):
        for finding in (
            {"text": "x", "basis": "observed", "evidenceIds": [99], "alertIds": []},
            {"text": "x", "basis": "observed", "evidenceIds": [], "alertIds": [1]},  # alerta sin texto
            {"text": "x", "basis": "observed", "evidenceIds": [], "alertIds": []},
        ):
            model = FakeModel(summary_output(findings=[finding]))
            with self.assertRaises(AiError) as caught:
                await SynthesizeIncidentSummary(model, load_prompt("summary_v1")).execute(self.source())
            self.assertEqual((caught.exception.code, caught.exception.retryable), ("invalid_model_output", True))

    def test_input_rules(self):
        cases = {
            "unknown_alert": lambda: SynthesisInput(
                5, (AlertContext(1, None, None, None, None),),
                (EvidenceInput(10, 9, Modality.IMAGE, None, analysis()),)),
            "nothing_to_summarize": lambda: SynthesisInput(5, (AlertContext(1, None, " ", None, None),), ()),
            "duplicate_source": lambda: SynthesisInput(
                5, (AlertContext(1, None, "x", None, None), AlertContext(1, None, "y", None, None)), ()),
        }
        for code, make in cases.items():
            with self.assertRaises(AiError) as caught:
                make()
            self.assertEqual(caught.exception.code, code)
        # Una alerta con descripción y sin archivos ya permite un resumen.
        self.assertFalse(SynthesisInput(5, (AlertContext(1, None, "Humo", None, None),), ()).is_single_evidence)


class SchemaTests(unittest.TestCase):
    def test_schemas_are_inlined_and_strict(self):
        schema = json_schema(VideoOutput)
        self.assertNotIn("$defs", schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("timeline", schema["required"])
        self.assertFalse(schema["properties"]["timeline"]["items"]["additionalProperties"])
