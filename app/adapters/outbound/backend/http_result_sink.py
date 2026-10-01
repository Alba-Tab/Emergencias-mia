"""Entrega autenticada del resultado al backend dueño del incidente."""

import asyncio

import httpx

from app.application.job_runner import JobError
from app.domain.analysis_result import AnalysisResult
from app.domain.evidence_reference import EvidenceReference


class HttpResultSink:
    def __init__(self, url: str, token: str, client: httpx.AsyncClient | None = None, timeout: float = 10.0) -> None:
        self.url = url
        self.token = token
        self.client = client
        self.timeout = timeout

    async def publish(self, result: AnalysisResult) -> None:
        await self._post({
            "jobId": result.job_id,
            "evidenceId": result.evidence_id,
            "alertId": result.alert_id,
            "incidentId": result.incident_id,
            "status": "completed",
            "result": {
                "summary": result.scene.summary,
                "observations": list(result.scene.observations),
                "risks": list(result.scene.risks),
                "limitations": list(result.scene.limitations),
                "provider": result.scene.provider,
                "model": result.scene.model,
                "promptVersion": result.scene.prompt_version,
                "analyzedAt": result.analyzed_at.isoformat(),
            },
        })

    async def publish_failure(self, job_id: str, evidence: EvidenceReference, code: str) -> None:
        await self._post({
            "jobId": job_id,
            "evidenceId": evidence.evidence_id,
            "alertId": evidence.alert_id,
            "incidentId": evidence.incident_id,
            "status": "failed",
            "errorCode": code,
        })

    async def _post(self, payload: dict) -> None:
        owned = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(3):
                try:
                    response = await client.post(
                        self.url,
                        headers={"Authorization": f"Bearer {self.token}"},
                        json=payload,
                    )
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(0.2 * 2**attempt)
                        continue
                    if response.status_code >= 400:
                        raise JobError("callback_failed")
                    return
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == 2:
                        raise JobError("callback_failed") from exc
                    await asyncio.sleep(0.2 * 2**attempt)
        finally:
            if owned:
                await client.aclose()
