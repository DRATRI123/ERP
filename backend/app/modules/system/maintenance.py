"""Maintenance mode: the API keeps serving reads but refuses every change to business data,
and the worker pauses. Used for the PostgreSQL cutover (and any other planned work), so the
data being copied cannot change underneath the copy.

Stored in ``system_settings`` (key ``maintenance``) so the CLI, every API process and the
worker see the same switch; API processes cache it for a few seconds.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.modules.system.models import SystemSetting

KEY = "maintenance"
DEFAULT_MESSAGE = "The ERP is in maintenance; changes are paused for a few minutes. Viewing still works."
# Still allowed while in maintenance: signing in/out (sessions are not business data) and
# switching maintenance off again.
ALLOWED_PREFIXES = ("/api/v1/auth/", "/api/v1/system/maintenance")


def get(db: Session) -> dict[str, Any]:
    row = db.get(SystemSetting, KEY)
    return row.value if row and row.value.get("enabled") else {"enabled": False}


def set_mode(db: Session, enabled: bool, message: str | None = None, by: str | None = None) -> dict[str, Any]:
    value = {"enabled": enabled, "message": message or DEFAULT_MESSAGE,
             "since": datetime.now(UTC).isoformat(), "by": by}
    row = db.get(SystemSetting, KEY)
    if row is None:
        db.add(SystemSetting(key=KEY, value=value))
    else:
        row.value = value
    cache.invalidate()
    return value


class _Cache:
    def __init__(self, ttl: float = 3.0) -> None:
        self.ttl, self._value, self._at = ttl, {"enabled": False}, 0.0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        self._at = 0.0

    def get(self) -> dict[str, Any]:
        if time.monotonic() - self._at < self.ttl:
            return self._value
        with self._lock:
            if time.monotonic() - self._at >= self.ttl:
                from app.db.session import SessionLocal, read_only

                with read_only():
                    db = SessionLocal()
                    try:
                        self._value = get(db)
                    except Exception:  # noqa: BLE001 - table missing before migrations: not in maintenance
                        self._value = {"enabled": False}
                    finally:
                        db.close()
                self._at = time.monotonic()
        return self._value


cache = _Cache()


def blocks(method: str, path: str) -> dict[str, Any] | None:
    """The maintenance state if this request must be refused, else None."""
    if method in ("GET", "HEAD", "OPTIONS") or path.startswith(ALLOWED_PREFIXES):
        return None
    state = cache.get()
    return state if state.get("enabled") else None
