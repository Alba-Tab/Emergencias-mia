"""Adaptador de visión OpenRouter con salida estructurada."""

import asyncio
import base64

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.job_runner import JobError
from app.domain.analysis_result import SceneAnalysis

PROMPT_VERSION = "image-v1"
PROMPT = (
    "Analiza la escena visible de una emergencia. Responde únicamente con hechos observables "
    "y riesgos posibles, sin identificar personas ni inferir lesiones, causas o gravedad no visibles. "
    "Es un análisis preliminar y no constituye un diagnóstico. Explicita incertidumbres y limitaciones."
)


class _ScenePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    observations: list[str]
    risks: list[str]
    limitations: list[str]


SCHEMA = _ScenePayload.model_json_schema()


class OpenRouterSceneAnalyzer:
    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model
        self.client = client
        self.timeout = timeout

    async def analyze(self, image: bytes, mime_type: str) -> SceneAnalysis:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "emergency_scene", "strict": True, "schema": SCHEMA,
            }},
            "provider": {"require_parameters": True},
        }
        owned = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(3):
                try:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(0.2 * 2**attempt)
                            continue
                        raise JobError("provider_unavailable")
                    if response.status_code >= 400:
                        raise JobError("provider_rejected")
                    try:
                        content = response.json()["choices"][0]["message"]["content"]
                        scene = _ScenePayload.model_validate_json(content)
                    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                        raise JobError("invalid_provider_response") from exc
                    return SceneAnalysis(
                        summary=scene.summary,
                        observations=tuple(scene.observations),
                        risks=tuple(scene.risks),
                        limitations=tuple(scene.limitations),
                        provider="openrouter",
                        model=self.model,
                        prompt_version=PROMPT_VERSION,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == 2:
                        raise JobError("provider_unavailable") from exc
                    await asyncio.sleep(0.2 * 2**attempt)
        finally:
            if owned:
                await client.aclose()
        raise JobError("provider_unavailable")
