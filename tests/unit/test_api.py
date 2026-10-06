import logging
import unittest
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import httpx

from app.adapters.inbound.http.dependencies import get_services, get_settings
from app.core.composition import Services
from app.core.config import Settings
from app.domain.analysis_result import AnalysisResult, Provenance
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality
from app.main import app
from app.schemas.summary import AnalysisIn, SummaryRequest
from tests.fixtures import analysis_payload, synthetic_png

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
SECRET_URL = "https://objects.example/image.png?signature=private"


class FakeAnalyze:
    def __init__(self, error=None):
        self.error = error
        self.seen = []

    async def execute(self, job_id, evidence):
        self.seen.append((job_id, evidence))
        if self.error:
            raise self.error
        return AnalysisResult(job_id, evidence.evidence_id, evidence.alert_id, evidence.incident_id, Modality.IMAGE,
                              AnalysisIn.model_validate(analysis_payload()).to_domain(),
                              Provenance("openrouter", "m", "image-v2", NOW, "model"))


class FakeSynthesize:
    def __init__(self):
        self.seen = []

    async def execute(self, source):
        from app.application.use_cases.synthesize_summary import SynthesizeIncidentSummary
        self.seen.append(source)
        return await SynthesizeIncidentSummary(None, None, lambda: NOW).execute(source)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.analyze = FakeAnalyze()
        self.synthesize = FakeSynthesize()
        app.dependency_overrides[get_settings] = lambda: Settings(service_token="service-secret")
        app.dependency_overrides[get_services] = lambda: Services(self.analyze, self.synthesize)
        self.payload = {
            "jobId": str(uuid4()), "evidenceId": 1, "alertId": 2, "incidentId": 3,
            "downloadUrl": SECRET_URL, "mimeType": "Image/PNG", "checksumSha256": sha256(synthetic_png()).hexdigest(),
        }

    def tearDown(self):
        app.dependency_overrides.clear()

    async def post(self, path, payload, token="service-secret"):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=payload, headers=headers)

    async def test_authentication(self):
        for path in ("/v1/analyses", "/v1/summaries"):
            for token in (None, "wrong"):
                response = await self.post(path, self.payload, token)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"errorCode": "unauthorized", "retryable": False})
        app.dependency_overrides[get_settings] = lambda: Settings(service_token=None)
        self.assertEqual((await self.post("/v1/analyses", self.payload)).status_code, 503)

    async def test_validation_never_echoes_secret_url(self):
        for field, value in (("jobId", "bad"), ("evidenceId", 0), ("checksumSha256", "bad"),
                             ("downloadUrl", "http://localhost/image.png"), ("bucket", "old-contract")):
            response = await self.post("/v1/analyses", {**self.payload, field: value})
            self.assertEqual(response.status_code, 422, field)
            self.assertEqual(response.json()["errorCode"], "invalid_request")
            self.assertNotIn("signature=private", response.text)
            self.assertNotIn("localhost", response.text)
        self.assertEqual(self.analyze.seen, [])

    async def test_analysis_success(self):
        response = await self.post("/v1/analyses", self.payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["evidenceId"], body["modality"], body["schemaVersion"]),
                         (1, "image", "evidence-analysis.v2"))
        self.assertEqual(body["analysis"]["severity"]["level"], "moderate")
        self.assertEqual(body["provenance"]["promptVersion"], "image-v2")
        self.assertEqual(self.analyze.seen[0][1].mime_type, "image/png")
        # El objeto `analysis` devuelto se acepta tal cual en la síntesis.
        AnalysisIn.model_validate(body["analysis"])

    async def test_analysis_error_mapping(self):
        for error, status in ((AiError("checksum_mismatch"), 422),
                              (AiError("provider_unavailable", retryable=True), 503),
                              (AiError("provider_rejected"), 502)):
            self.analyze.error = error
            response = await self.post("/v1/analyses", self.payload)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {"errorCode": error.code, "retryable": error.retryable})

    async def test_summary_single_evidence(self):
        payload = {
            "incidentId": 3,
            "alerts": [{"alertId": 2, "reportedAt": "2026-10-02T10:00:00Z"}],
            "evidences": [{"evidenceId": 1, "alertId": 2, "modality": "image", "analysis": analysis_payload()}],
        }
        response = await self.post("/v1/summaries", payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["schemaVersion"], "incident-summary.v2")
        self.assertEqual(body["summary"]["keyPoints"], [{"kind": "what", "text": "Choque entre dos autos."}])
        self.assertEqual(body["summary"]["hazards"], ["traffic"])
        self.assertEqual(body["summary"]["hazardStates"], [{"type": "traffic", "status": "active", "lastReportedAt": None}])
        self.assertEqual(body["summary"]["resolvedHazards"], [])
        self.assertEqual(body["usedEvidenceIds"], [1])
        self.assertEqual(body["provenance"]["method"], "single_evidence")
        self.assertEqual(body["summary"]["findings"][0]["corroboratingAlerts"], 1)

    def test_summary_accepts_v1_and_v2_analyses(self):
        v1 = {k: v for k, v in analysis_payload().items() if k not in ("usable", "unusableReason")}
        self.assertTrue(AnalysisIn.model_validate(v1).to_domain().usable)
        v2 = AnalysisIn.model_validate(analysis_payload(usable=False, unusableReason="silent")).to_domain()
        self.assertEqual((v2.usable, v2.unusable_reason), (False, "silent"))

    async def test_summary_input_errors(self):
        unknown_alert = {"incidentId": 3, "alerts": [{"alertId": 2}],
                         "evidences": [{"evidenceId": 1, "alertId": 9, "modality": "image", "analysis": analysis_payload()}]}
        response = await self.post("/v1/summaries", unknown_alert)
        self.assertEqual((response.status_code, response.json()["errorCode"]), (422, "unknown_alert"))
        response = await self.post("/v1/summaries", {"incidentId": 3, "alerts": []})
        self.assertEqual((response.status_code, response.json()["errorCode"]), (422, "invalid_request"))

    def test_request_logs_are_emitted_without_httpx_urls(self):
        self.assertTrue(logging.getLogger("app.adapters.inbound.http.analyses").isEnabledFor(logging.INFO))
        self.assertFalse(logging.getLogger("httpx").isEnabledFor(logging.INFO))

    def test_summary_accepts_any_alert_the_backend_accepts(self):
        request = SummaryRequest.model_validate({
            "incidentId": 3,
            "alerts": [{"alertId": 2, "description": "x" * 2000, "affectedCount": 50000}],
        })
        self.assertEqual(request.alerts[0].affectedCount, 50000)
