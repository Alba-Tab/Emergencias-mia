from fastapi import FastAPI
from app.core.config import settings
from app.adapters.inbound.http import health, jobs

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
    )

    # Incluir los adaptadores inbound (rutas HTTP)
    app.include_router(health.router)
    app.include_router(jobs.router)

    return app

app = create_app()
