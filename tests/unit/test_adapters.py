import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from app.adapters.outbound.backend.http_result_sink import HttpResultSink
from app.adapters.outbound.providers.openrouter_scene_analyzer import OpenRouterSceneAnalyzer
from app.adapters.outbound.storage.http_evidence_reader import HttpEvidenceReader, valid_download_url
from app.application.job_runner import EvidenceJobRunner, JobError
from app.application.use_cases.analyze_evidence import UnsupportedModalityError
from app.domain.analysis_result import AnalysisResult, SceneAnalysis
from app.domain.evidence_reference import EvidenceReference
from tests.fixtures import synthetic_png


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = EvidenceReference(1, 2, 3, "https://objects.example/key?signature=private", "image/png")

    async def test_reads_private_url_without_modifying_signature(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, content=synthetic_png(), headers={"Content-Type": "image/png"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            image = await HttpEvidenceReader(client=client).read(self.evidence)
        self.assertEqual(image, synthetic_png())
        self.assertEqual(seen, [self.evidence.download_url])

    async def test_rejects_unsafe_urls(self):
        for url in ("http://objects.example/key", "https://localhost/key", "https://127.0.0.1/key",
                    "https://user:pass@objects.example/key", "https://objects.example:8443/key",
                    "https://objects.example/key#fragment"):
            self.assertFalse(valid_download_url(url), url)
            evidence = EvidenceReference(1, 2, 3, url, "image/png")
            with self.assertRaisesRegex(JobError, "invalid_download_url"):
                await HttpEvidenceReader().read(evidence)

    async def test_rejects_missing_forbidden_empty_large_wrong_mime_and_redirect(self):
        cases = [
            (httpx.Response(404), "object_not_found", 1024),
            (httpx.Response(403), "download_forbidden", 1024),
            (httpx.Response(302, headers={"Location": "https://other.example"}), "download_failed", 1024),
            (httpx.Response(200, content=synthetic_png(), headers={"Content-Type": "image/jpeg"}), "mime_mismatch", 1024),
            (httpx.Response(200, content=synthetic_png(), headers={"Content-Type": "image/png"}), "image_too_large", 1),
        ]
        for response, code, limit in cases:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
                with self.assertRaisesRegex(JobError, code):
                    await HttpEvidenceReader(client=client, max_bytes=limit).read(self.evidence)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"", headers={"Content-Type": "image/png"}))) as client:
            self.assertEqual(await HttpEvidenceReader(client=client).read(self.evidence), b"")

    async def test_retries_transient_http_errors_and_timeout(self):
        for status_code in (429, 500):
            calls = []

            def handler(_):
                calls.append(1)
                return httpx.Response(status_code)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch("app.adapters.outbound.storage.http_evidence_reader.asyncio.sleep"):
                    with self.assertRaisesRegex(JobError, "download_unavailable"):
                        await HttpEvidenceReader(client=client).read(self.evidence)
            self.assertEqual(len(calls), 3)

        def timeout(_):
            raise httpx.ConnectTimeout("timeout")

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with patch("app.adapters.outbound.storage.http_evidence_reader.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "download_unavailable"):
                    await HttpEvidenceReader(client=client).read(self.evidence)


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
            await sink.publish_failure("job", EvidenceReference(1, 2, 3, "https://objects.example/key", "image/png"), "object_not_found")
        self.assertEqual([p["status"] for p in payloads], ["completed", "failed"])
        self.assertEqual(payloads[0]["result"]["promptVersion"], "image-v1")
        self.assertEqual(payloads[1]["errorCode"], "object_not_found")

    async def test_callback_failure(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            sink = HttpResultSink("http://local/callback", "token", client)
            with patch("app.adapters.outbound.backend.http_result_sink.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "callback_failed"):
                    await sink.publish_failure("job", EvidenceReference(1, 2, 3, "https://objects.example/key", "image/png"), "invalid_image")

    async def test_callback_timeout(self):
        def timeout(_):
            raise httpx.ConnectTimeout("timeout")

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            sink = HttpResultSink("http://local/callback", "token", client)
            with patch("app.adapters.outbound.backend.http_result_sink.asyncio.sleep"):
                with self.assertRaisesRegex(JobError, "callback_failed"):
                    await sink.publish_failure("job", EvidenceReference(1, 2, 3, "https://objects.example/key", "image/png"), "invalid_image")


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
        await EvidenceJobRunner(UseCase(), sink).run("job", EvidenceReference(1, 2, 3, "https://objects.example/key", "image/png"))
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
        await EvidenceJobRunner(UseCase(), sink).run("job", EvidenceReference(1, 2, 3, "https://objects.example/key", "audio/wav"))
        self.assertEqual(sink.code, "unsupported_media_type")

    async def test_does_not_log_download_url_on_unexpected_error(self):
        secret_url = "https://objects.example/key?signature=secret"

        class UseCase:
            async def execute(self, *_):
                raise RuntimeError(secret_url)

        class Sink:
            async def publish_failure(self, *_):
                pass

        with self.assertLogs("app.application.job_runner", level="ERROR") as logs:
            await EvidenceJobRunner(UseCase(), Sink()).run(
                "job", EvidenceReference(1, 2, 3, secret_url, "image/png"))
        self.assertNotIn(secret_url, " ".join(logs.output))
