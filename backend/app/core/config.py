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
    # Business calendar: decides which day "today" is for invoices, payments and reports.
    timezone: str = "Asia/Kolkata"
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

    sqlite_busy_timeout_ms: int = 15000
    # Connection pool per API process. SQLite (cheap connections) gets one per request thread
    # (40). PostgreSQL: every process may open pool + overflow connections, so
    # api_workers × (pool + overflow) + worker + headroom must stay below max_connections
    # (100 by default): 4 × 15 = 60. Requests beyond that wait briefly, then get 503.
    db_pool_size: int = 8
    db_max_overflow: int = 7
    db_pool_timeout_s: int = 10
    # API processes (uvicorn workers); used by check-config to size the PostgreSQL pool.
    api_workers: int = 2
    # Concurrent Argon2 password hashes (64 MiB each). Default: number of CPU cores.
    password_hash_concurrency: int = 0

    # PDF printing via headless Chromium. Leave empty to auto-detect on PATH.
    pdf_chromium_path: str | None = None
    # Needed when the server runs Chromium as root (typical in Docker). Rendering is offline
    # with JavaScript disabled, which limits the exposure.
    pdf_no_sandbox: bool = False
    cors_origins: list[str] = []

    # Redis (Phase 7): event stream for the real-time service and shared rate limits.
    # Optional: without it the API still works, rate limits are per process and the
    # real-time service receives nothing.
    redis_url: str | None = None
    event_stream: str = "erp:events"
    event_stream_maxlen: int = 100_000

    # Fernet keys for credentials at rest; first one encrypts (see app/core/crypto.py).
    encryption_keys: list[str] = []
    # Public base URL of this ERP (webhook and OAuth callback URLs are built from it).
    public_base_url: str = "http://localhost:8000"

    # ------------------------------------------------------------------ Phase 8: security & ops
    # Host names this server answers to (besides verified custom domains). Empty disables the
    # Host-header check (development only; production refuses to start without it).
    allowed_hosts: list[str] = []
    # Docker-internal names other services use to call the API directly over HTTP (the
    # real-time service calls http://api:8000). Allowed by the Host check, never redirected.
    internal_hosts: list[str] = ["api", "localhost", "127.0.0.1", "::1"]
    # Behind a TLS proxy: send HSTS and redirect plain-HTTP requests to HTTPS.
    force_https: bool = False
    # Shared secret Caddy appends to its on-demand TLS "ask" URL. Unset = endpoint disabled.
    tls_ask_token: str | None = None
    # Bearer token for Prometheus scraping of /metrics. Unset = endpoint disabled.
    metrics_token: str | None = None
    # Sign-in attempts allowed per client IP per 15 minutes (on top of per-account lockout).
    login_ip_limit: int = 30
    mfa_token_ttl_minutes: int = 5
    # Keep the refresh token in an httpOnly cookie (with a double-submit CSRF token) instead of
    # returning it in the JSON body. For the browser frontend.
    auth_cookie: bool = False

    # Encrypted backups: 32-byte key, base64 (``python -m app.cli backup-keygen``).
    backup_key: str | None = None
    backup_dir: str = "./data/backups"
    # Scratch PostgreSQL database for automated restore drills (its contents are replaced).
    # SQLite installs drill into a temporary file and need nothing.
    restore_drill_url: str | None = None
    # Minimum free disk space before readiness fails and backups refuse to run.
    min_free_disk_mb: int = 500

    # System e-mail (security alerts to users). Optional; without it alerts stay in-app.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_zone(cls, v: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except (KeyError, ValueError) as exc:  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"Unknown time zone {v!r} (use an IANA name such as Asia/Kolkata)") from exc
        return v

    @field_validator("backup_dir")
    @classmethod
    def _resolve_backup_dir(cls, v: str) -> str:
        path = Path(v)
        return str(path if path.is_absolute() else (BACKEND_DIR / path).resolve())

    @field_validator("allowed_hosts")
    @classmethod
    def _lower_hosts(cls, v: list[str]) -> list[str]:
        return [h.strip().lower().rstrip(".") for h in v if h.strip()]

    def check_production(self) -> list[str]:
        """Settings that must be set before serving real traffic."""
        problems = []
        if not self.allowed_hosts:
            problems.append("ERP_ALLOWED_HOSTS is empty")
        if not self.force_https:
            problems.append("ERP_FORCE_HTTPS is off")
        if not self.encryption_keys:
            problems.append("ERP_ENCRYPTION_KEYS is empty")
        if not self.backup_key:
            problems.append("ERP_BACKUP_KEY is empty")
        return problems

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
