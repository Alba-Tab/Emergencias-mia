"""Descarga acotada mediante una URL temporal entregada por el backend."""

import asyncio

import httpx

from app.core.net import valid_download_url
from app.domain.errors import AiError
from app.domain.evidence_reference import EvidenceReference


class HttpEvidenceReader:
    def __init__(self, client: httpx.AsyncClient, timeout: float = 20.0, attempts: int = 2) -> None:
        if timeout <= 0 or attempts <= 0:
            raise ValueError("timeout y attempts deben ser positivos")
        self.client = client
        self.timeout = timeout
        self.attempts = attempts

    async def read(self, evidence: EvidenceReference, max_bytes: int) -> bytes:
        if not valid_download_url(evidence.download_url):
            raise AiError("invalid_download_url")
        for attempt in range(self.attempts):
            last = attempt == self.attempts - 1
            try:
                # El timeout de httpx es por operación: un envío lento pero constante nunca lo dispara.
                return await asyncio.wait_for(self._read_once(evidence, max_bytes), self.timeout)
            except (_Transient, asyncio.TimeoutError) as exc:
                if last:
                    raise AiError("download_unavailable", retryable=True) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if last:
                    raise AiError("download_unavailable", retryable=True) from exc
            await asyncio.sleep(0.5 * 2**attempt)
        raise AiError("download_unavailable", retryable=True)

    async def _read_once(self, evidence: EvidenceReference, max_bytes: int) -> bytes:
        async with self.client.stream(
            "GET", evidence.download_url, follow_redirects=False, timeout=self.timeout,
        ) as response:
            if response.status_code == 429 or response.status_code >= 500:
                raise _Transient()
            if response.status_code in {401, 403}:
                # Normalmente la URL caducó: el backend puede firmar otra y reintentar.
                raise AiError("download_forbidden", retryable=True)
            if response.status_code == 404:
                raise AiError("object_not_found")
            if response.status_code != 200:
                raise AiError("download_failed")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != evidence.mime_type:
                raise AiError("mime_mismatch")
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise AiError("evidence_too_large")
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise AiError("evidence_too_large")
                chunks.append(chunk)
            return b"".join(chunks)


class _Transient(Exception):
    pass
