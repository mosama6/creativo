from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {
    "dev-session-secret-change-me",
    "dev-worker-secret-change-me",
    "change-me",
}


def _discover_env_file() -> str | None:
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        path = candidate / ".env"
        if path.is_file():
            return str(path)
        if (candidate / "apps").is_dir() and (candidate / "packages").is_dir():
            return None
    return None


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_discover_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://creativo:creativo@localhost:5433/creativo"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: str = "dev-session-secret-change-me"
    worker_shared_secret: str = "dev-worker-secret-change-me"
    storage_dir: str = "./data/objects"
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: str = ""
    worker_id: str = "worker-fixture-1"
    worker_advertise_url: str = "http://localhost:8100"
    worker_model_id: str = "fixture-image"
    worker_port: int = 8100
    orchestrator_id: str = "orchestrator-1"
    orchestrator_port: int = 8090
    dev_starting_credits: int = 40
    generation_rate_limit: int = 30
    generation_rate_window_seconds: int = 60
    upload_rate_limit: int = 20
    upload_rate_window_seconds: int = 60
    upload_max_bytes: int = 8_000_000
    session_ttl_seconds: int = Field(default=14 * 24 * 3600)
    idempotency_ttl_seconds: int = Field(default=24 * 3600)
    retry_delay_seconds: float = 1.0
    batch_fill_seconds: float = 3.0
    release_weights_after_job: bool = False
    sfw_only: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def dev_auth_enabled(self) -> bool:
        return self.app_env in {"development", "test"}

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def assert_production_safe(self) -> None:
        if self.app_env != "production":
            return
        if self.session_secret in _WEAK_SECRETS or len(self.session_secret) < 32:
            raise RuntimeError("SESSION_SECRET is not set for production")
        if self.worker_shared_secret in _WEAK_SECRETS or len(self.worker_shared_secret) < 32:
            raise RuntimeError("WORKER_SHARED_SECRET is not set for production")
        if self.dev_auth_enabled:
            raise RuntimeError("Development login cannot be enabled in production")


@lru_cache
def get_settings() -> PlatformSettings:
    return PlatformSettings()
