"""Contrato HTTP de aceptación de trabajos."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.pipelines.image_pipeline import SUPPORTED_IMAGE_MIME_TYPES


class JobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: UUID = Field(alias="jobId")
    evidence_id: int = Field(gt=0, alias="evidenceId")
    alert_id: int = Field(gt=0, alias="alertId")
    incident_id: int = Field(gt=0, alias="incidentId")
    download_url: str = Field(min_length=1, alias="downloadUrl")
    mime_type: str = Field(alias="mimeType")
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$", alias="checksumSha256")

    @property
    def is_supported_image(self) -> bool:
        return self.mime_type in SUPPORTED_IMAGE_MIME_TYPES
