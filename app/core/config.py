from typing import Literal

from pydantic import Field
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
    # "frames": fotogramas (cambios de escena + relleno uniforme) y audio con ffmpeg; "full": el video entero.
    video_mode: Literal["frames", "full"] = "frames"
    video_max_frames: int = Field(default=8, ge=2, le=32)
    video_scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    # Si no se pueden preparar los fotogramas: true envía el video completo; false responde unreadable_media.
    video_fallback_to_full: bool = True
    ffmpeg_timeout_seconds: float = Field(default=10.0, gt=0)
    # Control de silencio (necesita ffmpeg): un audio cuyo pico no llega a este nivel en dBFS, o cuyo sonido
    # por encima de él dura menos que el mínimo, se considera en silencio y no se envía al modelo.
    silence_max_volume_db: float = Field(default=-50.0, le=0)
    silence_min_audible_seconds: float = Field(default=0.3, ge=0)

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="AI_", extra="ignore"
    )


settings = Settings()
