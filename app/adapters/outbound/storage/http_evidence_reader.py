"""Descarga acotada mediante una URL temporal entregada por el backend."""

import asyncio
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from app.application.job_runner import JobError
from app.domain.evidence_reference import EvidenceReference


def valid_download_url(url: str) -> bool:
    """Acepta HTTPS público; la red de despliegue debe limitar destinos salientes."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.fragment:
            return False
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return False
        try:
            ip_address(host)
        except ValueError:
            return True
        return False
    except ValueError:
        return False


class HttpEvidenceReader:
    def __init__(self, client: httpx.AsyncClient | None = None, max_bytes: int = 10 * 1024 * 1024,
                 timeout: float = 15.0) -> None:
        if max_bytes <= 0 or timeout <= 0:
            raise ValueError("Los límites de descarga deben ser positivos")
        self.client = client
        self.max_bytes = max_bytes
        self.timeout = timeout

    async def read(self, evidence: EvidenceReference) -> bytes:
        if not valid_download_url(evidence.download_url):
            raise JobError("invalid_download_url")
        owned = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        try:
            for attempt in range(3):
                try:
                    async with client.stream("GET", evidence.download_url, follow_redirects=False) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt < 2:
                                await asyncio.sleep(0.2 * 2**attempt)
                                continue
                            raise JobError("download_unavailable")
                        if response.status_code in {401, 403}:
                            raise JobError("download_forbidden")
                        if response.status_code == 404:
                            raise JobError("object_not_found")
                        if response.status_code != 200:
                            raise JobError("download_failed")
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if content_type != evidence.mime_type:
                            raise JobError("mime_mismatch")
                        content_length = response.headers.get("Content-Length")
                        if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
                            raise JobError("image_too_large")
                        chunks = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise JobError("image_too_large")
                            chunks.append(chunk)
                        return b"".join(chunks)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == 2:
                        raise JobError("download_unavailable") from exc
                    await asyncio.sleep(0.2 * 2**attempt)
        finally:
            if owned:
                await client.aclose()
        raise JobError("download_unavailable")
