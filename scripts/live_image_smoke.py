"""Prueba optativa real S3 -> OpenRouter -> callback local."""

import asyncio
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Thread
from uuid import uuid4

import boto3
import httpx

from app.adapters.inbound.http.jobs import get_settings
from app.core.config import Settings
from app.main import app
from tests.fixtures import synthetic_png


async def main() -> None:
    config = Settings()
    if not config.s3_bucket or not config.openrouter_api_key:
        raise SystemExit("Configura AI_S3_BUCKET y AI_OPENROUTER_API_KEY antes del ensayo real")

    received = []
    callback_token = secrets.token_urlsafe(32)

    class CallbackReceiver(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/callback" or self.headers.get("Authorization") != f"Bearer {callback_token}":
                self.send_error(401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackReceiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = secrets.token_urlsafe(32)
    local_config = config.model_copy(update={
        "service_token": token,
        "callback_url": f"http://127.0.0.1:{server.server_port}/callback",
        "callback_token": callback_token,
    })
    app.dependency_overrides[get_settings] = lambda: local_config
    object_key = f"ai-smoke-exclusive/{uuid4()}.png"
    image = synthetic_png()
    uploaded = False
    client = None
    try:
        client = boto3.client("s3", region_name=config.aws_region)
        client.put_object(Bucket=config.s3_bucket, Key=object_key, Body=image, ContentType="image/png")
        uploaded = True
        job_id = str(uuid4())
        payload = {
            "jobId": job_id, "evidenceId": 1, "alertId": 1, "incidentId": 1,
            "bucket": config.s3_bucket, "objectKey": object_key, "mimeType": "image/png",
            "checksumSha256": sha256(image).hexdigest(),
        }
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as http:
            response = await http.post("/v1/jobs", json=payload, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 202, f"Aceptación fallida: {response.status_code}"
        assert len(received) == 1, "No llegó exactamente un callback"
        assert received[0]["jobId"] == job_id and received[0]["status"] == "completed", received[0].get("errorCode", "callback inválido")
        assert received[0]["result"]["provider"] == "openrouter"
        print("Ensayo real correcto: S3, OpenRouter y callback local")
    finally:
        if uploaded and client is not None:
            client.delete_object(Bucket=config.s3_bucket, Key=object_key)
        app.dependency_overrides.clear()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
