"""Ensayo real: URL de descarga del backend -> análisis con OpenRouter -> síntesis del incidente.

La síntesis agrega una segunda alerta con descripción escrita para forzar la llamada al modelo.
"""

import asyncio
import json
import os
import secrets
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from app.adapters.inbound.http.dependencies import get_settings
from app.core.config import Settings
from app.main import app


async def main() -> None:
    load_dotenv()
    config = Settings()
    download_url = os.getenv("AI_SMOKE_DOWNLOAD_URL")
    checksum = os.getenv("AI_SMOKE_SHA256")
    mime_type = os.getenv("AI_SMOKE_MIME_TYPE", "image/png")
    if not config.openrouter_api_key or not download_url or not checksum:
        raise SystemExit("Configura AI_OPENROUTER_API_KEY, AI_SMOKE_DOWNLOAD_URL y AI_SMOKE_SHA256")

    token = secrets.token_urlsafe(32)
    app.dependency_overrides[get_settings] = lambda: config.model_copy(update={"service_token": token})
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                                         timeout=300) as http:
                analysis = await http.post("/v1/analyses", headers=headers, json={
                    "jobId": str(uuid4()), "evidenceId": 1, "alertId": 1, "incidentId": 1,
                    "downloadUrl": download_url, "mimeType": mime_type, "checksumSha256": checksum,
                })
                assert analysis.status_code == 200, f"Análisis fallido: {analysis.status_code} {analysis.text}"
                body = analysis.json()
                print(json.dumps(body, ensure_ascii=False, indent=2))

                summary = await http.post("/v1/summaries", headers=headers, json={
                    "incidentId": 1,
                    "alerts": [
                        {"alertId": 1, "reportedAt": "2026-10-02T10:00:00Z"},
                        {"alertId": 2, "reportedAt": "2026-10-02T10:03:00Z",
                         "description": "Estoy cerca, veo a una persona en el suelo que no se mueve", "affectedCount": 1},
                    ],
                    "evidences": [{"evidenceId": 1, "alertId": 1, "modality": body["modality"],
                                   "receivedAt": "2026-10-02T10:01:00Z", "analysis": body["analysis"]}],
                })
                assert summary.status_code == 200, f"Síntesis fallida: {summary.status_code} {summary.text}"
                result = summary.json()
                print(json.dumps(result, ensure_ascii=False, indent=2))
                assert result["provenance"]["method"] == "model"
                assert result["usedEvidenceIds"] == [1]
        print("Ensayo real correcto: descarga temporal, análisis y síntesis con OpenRouter")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    asyncio.run(main())
