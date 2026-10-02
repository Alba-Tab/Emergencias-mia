from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    app_name: str = "Emergencias AI API"
    version: str = "0.2.0"
    service_token: str | None = None
    # "prueba" usa un analizador sin proveedor real: no necesita clave ni tiene costo.
    provider: Literal["openrouter", "prueba"] = "openrouter"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-3.8-flash"
    # Opcionales: un modelo distinto por tarea; si faltan se usa openrouter_model.
    image_model: str | None = None
    audio_model: str | None = None
    video_model: str | None = None
    summary_model: str | None = None
    provider_timeout_seconds: float = 90.0
    download_timeout_seconds: float = 20.0
    max_concurrent_model_calls: int = 4
    image_max_bytes: int = 10 * MIB
    audio_max_bytes: int = 5 * MIB  # mismo límite que el backend
    audio_max_seconds: float = 120.0
    video_max_bytes: int = 20 * MIB
    video_max_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="AI_", extra="ignore"
    )


settings = Settings()
