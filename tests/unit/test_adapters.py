import json
import unittest
from unittest.mock import patch

import httpx

from app.adapters.outbound.providers.openrouter_model import OpenRouterModel, media_content
from app.adapters.outbound.storage.http_evidence_reader import HttpEvidenceReader
from app.application.ports.multimodal_model import MediaPart
from app.core.net import valid_download_url
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference, Modality
from tests.fixtures import synthetic_png

SLEEP_READER = "app.adapters.outbound.storage.http_evidence_reader.asyncio.sleep"
SLEEP_MODEL = "app.adapters.outbound.providers.openrouter_model.asyncio.sleep"


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = EvidenceReference(1, 2, 3, "https://objects.example/key?signature=private", "image/png", "0" * 64)

    async def read(self, handler, max_bytes=1024):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch(SLEEP_READER):
                return await HttpEvidenceReader(client).read(self.evidence, max_bytes)

    async def assert_code(self, handler, code, retryable, max_bytes=1024):
        with self.assertRaises(AiError) as caught:
            await self.read(handler, max_bytes)
        self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable))

    async def test_reads_url_unchanged(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, content=synthetic_png(), headers={"Content-Type": "image/png"})

        self.assertEqual(await self.read(handler), synthetic_png())
        self.assertEqual(seen, [self.evidence.download_url])

    async def test_rejects_unsafe_urls(self):
        for url in ("http://objects.example/key", "https://localhost/key", "https://127.0.0.1/key",
                    "https://user:pass@objects.example/key", "https://objects.example:8443/key",
                    "https://objects.example/key#fragment"):
            self.assertFalse(valid_download_url(url), url)

    async def test_error_codes(self):
        png = synthetic_png()
        await self.assert_code(lambda _: httpx.Response(404), "object_not_found", False)
        await self.assert_code(lambda _: httpx.Response(403), "download_forbidden", True)
        await self.assert_code(lambda _: httpx.Response(302, headers={"Location": "https://x.example"}),
                               "download_failed", False)
        await self.assert_code(lambda _: httpx.Response(200, content=png, headers={"Content-Type": "image/jpeg"}),
                               "mime_mismatch", False)
        await self.assert_code(lambda _: httpx.Response(200, content=png, headers={"Content-Type": "image/png"}),
                               "evidence_too_large", False, max_bytes=1)

    async def test_retries_transient_then_fails_retryable(self):
        calls = []

        def handler(_):
            calls.append(1)
            return httpx.Response(503)

        await self.assert_code(handler, "download_unavailable", True)
        self.assertEqual(len(calls), 2)

        def timeout(_):
            raise httpx.ConnectTimeout("timeout")

        await self.assert_code(timeout, "download_unavailable", True)


def completion(content, **extra):
    return httpx.Response(200, json={"model": "google/test", "choices": [{"message": {"content": content}}], **extra})


class OpenRouterTests(unittest.IsolatedAsyncioTestCase):
    async def generate(self, handler, media=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch(SLEEP_MODEL):
                return await OpenRouterModel("key", "google/test", client).generate(
                    instructions="reglas", text="analiza", media=media, schema_name="s", schema={"type": "object"})

    def test_media_content_by_modality(self):
        image = media_content(MediaPart(Modality.IMAGE, "image/png", b"x"))
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
        audio = media_content(MediaPart(Modality.AUDIO, "audio/x-m4a", b"x"))
        self.assertEqual(audio["input_audio"]["format"], "m4a")
        video = media_content(MediaPart(Modality.VIDEO, "video/quicktime", b"x"))
        self.assertTrue(video["video_url"]["url"].startswith("data:video/mov;base64,"))

    async def test_payload_and_reply(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return completion('```json\n{"ok": true}\n```')

        reply = await self.generate(handler, MediaPart(Modality.IMAGE, "image/png", synthetic_png()))
        self.assertEqual((reply.content, reply.provider, reply.model), ({"ok": True}, "openrouter", "google/test"))
        payload = seen[0]
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "reglas"})
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertTrue(payload["provider"]["require_parameters"])

    async def test_error_mapping(self):
        cases = [
            (lambda _: httpx.Response(429), "provider_unavailable", True),
            (lambda _: httpx.Response(400), "provider_rejected", False),
            (lambda _: httpx.Response(402), "provider_misconfigured", False),
            (lambda _: completion("no es json"), "invalid_model_output", True),
            (lambda _: httpx.Response(200, json={"error": {"message": "upstream"}}), "provider_unavailable", True),
        ]
        for handler, code, retryable in cases:
            with self.assertRaises(AiError) as caught:
                await self.generate(handler)
            self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable), code)

    async def test_retries_once_on_transient(self):
        responses = [httpx.Response(503), completion('{"ok": 1}')]
        self.assertEqual((await self.generate(lambda _: responses.pop(0))).content, {"ok": 1})
