"""Application configuration loaded from environment variables (prefix: NPLAY_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NPLAY_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000

    db_path: str = "backend/data/nplaystudio.db"
    upload_dir: str = "uploads"
    output_dir: str = "outputs"

    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "nplaystudio"

    max_upload_mb: int = 512
    max_merge_files: int = 10
    file_ttl_seconds: int = 3600
    cleanup_interval_seconds: int = 300

    cors_origins: str = "*"

    # Run jobs in-process instead of pushing to Redis/ARQ (development only).
    inline_worker: bool = False

    @property
    def db_file(self) -> Path:
        return self._resolve(self.db_path)

    @property
    def upload_path(self) -> Path:
        return self._resolve(self.upload_dir)

    @property
    def output_path(self) -> Path:
        return self._resolve(self.output_dir)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @staticmethod
    def _resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (REPO_ROOT / path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
