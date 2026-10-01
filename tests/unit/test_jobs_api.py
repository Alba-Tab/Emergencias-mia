import unittest
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.adapters.inbound.http.jobs import get_settings
from app.core.config import Settings
from app.main import app
from tests.fixtures import synthetic_png


class JobsApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Settings(
            service_token="service-secret", s3_bucket="allowed", openrouter_api_key="provider-secret",
            callback_url="http://localhost/callback", callback_token="callback-secret",
        )
        app.dependency_overrides[get_settings] = lambda: self.config
        self.payload = {
            "jobId": str(uuid4()), "evidenceId": 1, "alertId": 2, "incidentId": 3,
            "bucket": "allowed", "objectKey": "test/image.png", "mimeType": "image/png",
            "checksumSha256": sha256(synthetic_png()).hexdigest(),
        }

    def tearDown(self):
        app.dependency_overrides.clear()

    async def post(self, payload=None, token="service-secret"):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/v1/jobs", json=payload or self.payload, headers=headers)

    async def test_authentication(self):
        self.assertEqual((await self.post(token=None)).status_code, 401)
        self.assertEqual((await self.post(token="wrong")).status_code, 401)

    async def test_validation(self):
        for field, value in (("jobId", "bad"), ("evidenceId", 0), ("mimeType", "video/mp4"),
                             ("checksumSha256", "bad"), ("bucket", "other")):
            payload = {**self.payload, field: value}
            self.assertEqual((await self.post(payload)).status_code, 422, field)
        self.config.callback_url = None
        self.assertEqual((await self.post()).status_code, 503)
        self.config.callback_url = "http://remote.example/callback"
        self.assertEqual((await self.post()).status_code, 503)

    async def test_accepts_and_runs_job(self):
        seen = []

        class Reader:
            def __init__(self, *_):
                pass

            async def read(self, _):
                return synthetic_png()

        class Analyzer:
            def __init__(self, *_ , **__):
                pass

            async def analyze(self, *_):
                from app.domain.analysis_result import SceneAnalysis
                return SceneAnalysis("escena", (), (), (), "openrouter", "test", "image-v1")

        class Sink:
            def __init__(self, *_ , **__):
                pass

            async def publish(self, result):
                seen.append(result)

            async def publish_failure(self, *_):
                self.fail("unexpected failure")

        with patch("app.adapters.inbound.http.jobs.S3EvidenceReader", Reader), \
             patch("app.adapters.inbound.http.jobs.OpenRouterSceneAnalyzer", Analyzer), \
             patch("app.adapters.inbound.http.jobs.HttpResultSink", Sink):
            response = await self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"jobId": self.payload["jobId"], "status": "accepted"})
        self.assertEqual(len(seen), 1)
