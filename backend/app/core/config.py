"""Application settings, loaded from environment variables (prefix ``ERP_``) or ``backend/.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ERP_", env_file=BACKEND_DIR / ".env", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    # Required: there is deliberately no default so a weak key can never ship.
    secret_key: str = Field(min_length=32)
    database_url: str = "sqlite:///./data/erp.db"

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    invite_token_ttl_hours: int = 72
    password_reset_ttl_hours: int = 2

    max_users_per_business: int = 30
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    password_min_length: int = 10

    sqlite_busy_timeout_ms: int = 5000
    cors_origins: list[str] = []

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, v: str) -> str:
        prefix = "sqlite:///"
        if v.startswith(prefix) and not v.startswith(prefix + "/") and ":memory:" not in v:
            path = (BACKEND_DIR / v[len(prefix):]).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{path}"
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
