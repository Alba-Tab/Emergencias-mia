"""Lectura privada y acotada de un objeto S3."""

import asyncio

import boto3
from botocore.exceptions import ClientError

from app.application.job_runner import JobError
from app.domain.evidence_reference import EvidenceReference


class S3EvidenceReader:
    def __init__(self, bucket: str, region: str | None = None, client=None, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.bucket = bucket
        self.max_bytes = max_bytes
        self.client = client or boto3.client("s3", region_name=region)

    async def read(self, evidence: EvidenceReference) -> bytes:
        if evidence.bucket != self.bucket:
            raise JobError("bucket_not_allowed")
        return await asyncio.to_thread(self._read, evidence)

    def _read(self, evidence: EvidenceReference) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=evidence.object_key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise JobError("object_not_found") from exc
            raise JobError("storage_error") from exc
        body = response["Body"]
        try:
            if response.get("ContentLength", 0) > self.max_bytes:
                raise JobError("image_too_large")
            if response.get("ContentType") != evidence.mime_type:
                raise JobError("mime_mismatch")
            data = body.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise JobError("image_too_large")
            return data
        finally:
            body.close()
