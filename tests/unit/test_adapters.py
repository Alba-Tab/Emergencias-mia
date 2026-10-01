import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
from botocore.exceptions import ClientError

from app.adapters.outbound.backend.http_result_sink import HttpResultSink
from app.adapters.outbound.providers.openrouter_scene_analyzer import OpenRouterSceneAnalyzer
from app.adapters.outbound.storage.s3_evidence_reader import S3EvidenceReader
from app.application.job_runner import EvidenceJobRunner, JobError
from app.application.use_cases.analyze_evidence import UnsupportedModalityError
from app.domain.analysis_result import AnalysisResult, SceneAnalysis
from app.domain.evidence_reference import EvidenceReference
from tests.fixtures import synthetic_png


class FakeS3:
    def __init__(self, data=None, content_type="image/png", error=None):
        self.data, self.content_type, self.error = data, content_type, error

    def get_object(self, **kwargs):
        if self.error:
            raise self.error
        return {"Body": io.BytesIO(self.data), "ContentLength": len(self.data), "ContentType": self.content_type}


class S3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = EvidenceReference(1, 2, 3, "allowed", "key", "image/png")

    async def test_reads_allowed_bucket(self):
        self.assertEqual(await S3EvidenceReader("allowed", client=FakeS3(synthetic_png())).read(self.evidence), synthetic_png())

    async def test_rejects_other_bucket(self):
        with self.assertRaisesRegex(JobError, "bucket_not_allowed"):
            await S3EvidenceReader("other", client=FakeS3(synthetic_png())).read(self.evidence)

    async def test_rejects_missing_empty_large_and_wrong_mime(self):
        cases = [
            (FakeS3(error=ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")), "object_not_found"),
            (FakeS3(synthetic_png(), content_type="image/jpeg"), "mime_mismatch"),
            (FakeS3(synthetic_png()), "image_too_large"),
        ]
        for client, code in cases:
            limit = 1 if code == "image_too_large" else 1024
            with self.assertRaisesRegex(JobError, code):
                await S3EvidenceReader("allowed", client=client, max_bytes=limit).read(self.evidence)
        self.assertEqual(await S3EvidenceReader("allowed", client=FakeS3(b"")).read(self.evidence), b"")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_request_and_metadata(self):
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertTrue(body["response_format"]["json_schema"]["strict"])
            self.assertTrue(body["provider"]["require_parameters"])
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "summary": "Escena de prueba", "observations": ["Un píxel"], "risks": [], "limitations": ["Imagen sintética"]
            })}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            scene = await OpenRouterSceneAnalyzer("secret", "test-model", client).analyze(synthetic_png(), "image/png")
        self.assertEqual(scene.model, "test-model")
        self.assertEqual(scene.provider, "openrouter")
        self.assertEqual(scene.prompt_version, "image-v1")
        self.assertEqual(len(seen), 1)

    async def test_invalid_response_and_retries(self):
        for status_code in (429, 500):
            calls = []

            def handler(request):
                calls.append(1)
                return httpx.Response(status_code)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch("app.adapters.outbound.providers.openrouter_scene_analyzer.asyncio.sleep"):
                    with self.assertRaisesRegex(JobError, "provider_unavailable"):
                        await OpenRouterSceneAnalyzer("secret", "model", client).analyze(synthetic_png(), "image/png")
            self.assertEqual(len(calls), 3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []}))) as client:
            with self.assertRaisesRegex(JobError, "invalid_provider_response"):
                await OpenRouterSceneAnalyzer("secret", "model", client).analyze(synthetic_png(), "image/png")

    async def test_timeout(self):
        def timeout(_):
            raise httpx.ConnectTimeout("timeout")

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with patch("app.adapters.outbound.providers.openrouter_scene_analyzer.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "provider_unavailable"):
                    await OpenRouterSceneAnalyzer("secret", "model", client).analyze(synthetic_png(), "image/png")


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_and_failed_payloads(self):
        payloads = []

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer callback-secret")
            payloads.append(json.loads(request.content))
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sink = HttpResultSink("http://local/callback", "callback-secret", client)
            scene = SceneAnalysis("resumen", (), (), (), "openrouter", "model", "image-v1")
            await sink.publish(AnalysisResult("job", 1, 2, 3, scene, datetime.now(timezone.utc)))
            await sink.publish_failure("job", EvidenceReference(1, 2, 3, "bucket", "key", "image/png"), "object_not_found")
        self.assertEqual([p["status"] for p in payloads], ["completed", "failed"])
        self.assertEqual(payloads[0]["result"]["promptVersion"], "image-v1")
        self.assertEqual(payloads[1]["errorCode"], "object_not_found")

    async def test_callback_failure(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            sink = HttpResultSink("http://local/callback", "token", client)
            with patch("app.adapters.outbound.backend.http_result_sink.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "callback_failed"):
                    await sink.publish_failure("job", EvidenceReference(1, 2, 3, "bucket", "key", "image/png"), "invalid_image")

    async def test_callback_timeout(self):
        def timeout(_):
            raise httpx.ConnectTimeout("timeout")

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            sink = HttpResultSink("http://local/callback", "token", client)
            with patch("app.adapters.outbound.backend.http_result_sink.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "callback_failed"):
                    await sink.publish_failure("job", EvidenceReference(1, 2, 3, "bucket", "key", "image/png"), "invalid_image")


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_failure(self):
        class UseCase:
            async def execute(self, *_):
                raise JobError("object_not_found")

        class Sink:
            def __init__(self):
                self.code = None

            async def publish_failure(self, _job, _evidence, code):
                self.code = code

        sink = Sink()
        await EvidenceJobRunner(UseCase(), sink).run("job", EvidenceReference(1, 2, 3, "bucket", "key", "image/png"))
        self.assertEqual(sink.code, "object_not_found")

    async def test_reports_unimplemented_modality(self):
        class UseCase:
            async def execute(self, *_):
                raise UnsupportedModalityError("audio")

        class Sink:
            code = None

            async def publish_failure(self, _job, _evidence, code):
                self.code = code

        sink = Sink()
        await EvidenceJobRunner(UseCase(), sink).run("job", EvidenceReference(1, 2, 3, "bucket", "key", "audio/wav"))
        self.assertEqual(sink.code, "unsupported_media_type")
