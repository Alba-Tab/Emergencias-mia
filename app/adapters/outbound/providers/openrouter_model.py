"""Adaptador OpenRouter para texto, imagen, audio y video con salida JSON estructurada."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx

from app.application.ports.multimodal_model import MediaPart, ModelReply
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality

logger = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# Nombres de formato que OpenRouter espera en `input_audio.format`.
AUDIO_FORMATS = {
    "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/mpeg": "mp3",
    "audio/aac": "aac", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg",
}
# OpenRouter documenta `video/mov`; iOS etiqueta el mismo contenedor como `video/quicktime`.
VIDEO_MIME_ALIASES = {"video/quicktime": "video/mov"}


def media_content(media: MediaPart) -> dict[str, Any]:
    encoded = base64.b64encode(media.data).decode("ascii")
    if media.modality is Modality.IMAGE:
        return {"type": "image_url", "image_url": {"url": f"data:{media.mime_type};base64,{encoded}"}}
    if media.modality is Modality.AUDIO:
        audio_format = AUDIO_FORMATS.get(media.mime_type)
        if audio_format is None:
            raise AiError("unsupported_media_type")
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}
    mime_type = VIDEO_MIME_ALIASES.get(media.mime_type, media.mime_type)
    return {"type": "video_url", "video_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def _json_content(body: dict[str, Any]) -> dict[str, Any]:
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):  # algunos proveedores devuelven partes de texto
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Se esperaba un objeto JSON")
    return parsed


class OpenRouterModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
        limiter: asyncio.Semaphore | None = None,
        timeout: float = 90.0,
        attempts: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.client = client
        self.limiter = limiter or asyncio.Semaphore(4)
        self.timeout = timeout
        self.attempts = attempts

    async def generate(
        self, *, instructions: str, text: str, media: MediaPart | None, schema_name: str, schema: dict[str, Any],
    ) -> ModelReply:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if media is not None:
            user_content.append(media_content(media))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
            "temperature": 0.2,
        }
        async with self.limiter:
            for attempt in range(self.attempts):
                last = attempt == self.attempts - 1
                try:
                    response = await self.client.post(
                        ENDPOINT, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload, timeout=self.timeout,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if last:
                        raise AiError("provider_unavailable", retryable=True) from exc
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                if response.status_code in {408, 429} or response.status_code >= 500:
                    if last:
                        raise AiError("provider_unavailable", retryable=True)
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                if response.status_code in {401, 402, 403}:
                    raise AiError("provider_misconfigured")
                if response.status_code >= 400:
                    raise AiError("provider_rejected")
                return self._reply(response)
        raise AiError("provider_unavailable", retryable=True)

    def _reply(self, response: httpx.Response) -> ModelReply:
        try:
            body = response.json()
        except ValueError as exc:
            raise AiError("invalid_model_output", retryable=True) from exc
        if isinstance(body, dict) and body.get("error"):
            # OpenRouter puede devolver 200 con un error del proveedor subyacente.
            raise AiError("provider_unavailable", retryable=True)
        try:
            content = _json_content(body)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise AiError("invalid_model_output", retryable=True) from exc
        usage = body.get("usage") or {}
        logger.info("Modelo %s: tokens entrada=%s salida=%s", body.get("model", self.model),
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return ModelReply(content=content, provider="openrouter", model=body.get("model") or self.model)
