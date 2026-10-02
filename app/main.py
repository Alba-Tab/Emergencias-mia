import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.adapters.inbound.http import analyses, health, summaries
from app.adapters.inbound.http.errors import install_error_handlers
from app.core.composition import build_services
from app.core.config import settings


def configure_logging() -> None:
    """Uvicorn solo configura sus propios loggers: sin esto, los `logger.info` de `app` no salen."""
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    # httpx registra en INFO cada URL que pide, y la de descarga es un secreto temporal.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Un cliente HTTP compartido reutiliza conexiones hacia el almacenamiento y el proveedor.
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.services = build_services(settings, client)
        yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(analyses.router)
    app.include_router(summaries.router)
    return app


app = create_app()
