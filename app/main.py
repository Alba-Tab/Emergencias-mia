from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.adapters.inbound.http import analyses, health, summaries
from app.adapters.inbound.http.errors import install_error_handlers
from app.core.composition import build_services
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Un cliente HTTP compartido reutiliza conexiones hacia el almacenamiento y el proveedor.
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.services = build_services(settings, client)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(analyses.router)
    app.include_router(summaries.router)
    return app


app = create_app()
