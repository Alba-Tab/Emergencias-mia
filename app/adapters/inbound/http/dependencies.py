"""Autenticación entre servicios y acceso a los casos de uso compuestos al arrancar."""

import hmac

from fastapi import Depends, Header, Request

from app.core.composition import Services
from app.core.config import Settings, settings
from app.domain.errors import AiError


def get_settings() -> Settings:
    return settings


def authorize(authorization: str | None = Header(default=None), config: Settings = Depends(get_settings)) -> None:
    if not config.service_token:
        raise AiError("service_not_configured")
    expected = f"Bearer {config.service_token}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise AiError("unauthorized")


def get_services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise AiError("service_not_configured")
    return services
