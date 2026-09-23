from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_UPLOAD_SIZE_MB: int = 25
    # All workers must share this file; production uses the app_data volume.
    EXTRACTION_LOCK_PATH: str = "/tmp/resume-normalizer-extraction.lock"

    # OpenAI
    OPENAI_API_KEY: SecretStr
    OPENAI_MODEL: str = "gpt-4.1-mini"
    OPENAI_MAX_RETRIES: int = 2
    OPENAI_TEMPERATURE: float = 0.2

    # AWS / S3 (credentials optional — uses instance role on EC2)
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_REGION: str = "ap-southeast-1"
    AWS_ENDPOINT_URL: str | None = None
    S3_EXTERNAL_URL: str | None = None  # Public-facing URL for presigned links (e.g. http://localhost:4566)
    S3_BUCKET_UPLOADS: str
    S3_BUCKET_GENERATED: str
    S3_PRESIGNED_URL_EXPIRY: int = 3600

    # Database
    DATABASE_URL: str = "sqlite:///./api_keys.db"

    # Auth
    ADMIN_API_KEY: SecretStr

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 20

    # OCR
    OCR_MIN_TEXT_LENGTH: int = 50

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    ALLOWED_MIME_TYPES: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/png",
        "image/jpeg",
    ]
