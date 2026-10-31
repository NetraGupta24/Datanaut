from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_DIR = Path(__file__).resolve().parent.parent

# Hard ceiling for GET /datasets/{id}/rows; the frontend asks for at most this many rows
MAX_ROWS_PER_REQUEST = 50_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SERVICE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Path to the Firebase service account JSON (never commit it)
    google_application_credentials: str | None = None
    # Optional: taken from the service account when unset
    firebase_project_id: str | None = None

    # Comma-separated list of allowed browser origins
    cors_origins: str = "http://localhost:5173"

    # Where LocalStorage keeps Parquet files (relative paths resolve against chatbot-service/)
    data_dir: Path = SERVICE_DIR / "data"

    max_upload_mb: int = 50
    preview_rows: int = 50
    # Loaded DataFrames kept in memory (LRU) for /rows and /chat
    dataframe_cache_size: int = 10

    # Chat (Groq). The key stays on the server; the browser never sees it.
    groq_api_key: SecretStr | None = None
    # llama-3.3-70b-versatile is no longer served by Groq; gpt-oss-120b handles tool calls well
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_s: float = 30.0
    chat_rate_limit: str = "20/minute"

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (SERVICE_DIR / value).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
