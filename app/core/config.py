from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Emergencias AI API"
    version: str = "0.1.0"
    service_token: str | None = None
    s3_bucket: str | None = None
    aws_region: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-3.8-flash"
    callback_url: str | None = None
    callback_token: str | None = None
    openrouter_timeout_seconds: float = 30.0
    callback_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="AI_", extra="ignore"
    )

settings = Settings()
