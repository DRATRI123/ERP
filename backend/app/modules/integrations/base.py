"""Connector plumbing: provider registry, HTTP client, call logging, signature helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

import httpx
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.integrations.models import Integration, IntegrationLog


class IntegrationError(AppError):
    """The external service refused or failed. Never raised for our own validation."""

    status_code = 502
    code = "integration_error"


# Tests (and the worker) can swap the transport; production uses real HTTPS.
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


def http_client(base_url: str = "", **kw: Any) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=httpx.Timeout(20.0, connect=10.0),
                        transport=_transport, **kw)


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    code: str
    label: str
    category: str  # communication / payments / google / commerce
    fields: tuple[Field, ...]
    capabilities: tuple[str, ...]
    webhook: bool = False
    oauth: bool = False
    docs: str = ""
    connector: Callable[..., Any] | None = field(default=None, compare=False)

    @property
    def secret_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.secret]


REGISTRY: dict[str, ProviderSpec] = {}


def register(spec: ProviderSpec) -> ProviderSpec:
    REGISTRY[spec.code] = spec
    return spec


class CallLog:
    """Context manager recording one outbound call in ``integration_logs``."""

    def __init__(self, db: Session, integration: Integration, operation: str) -> None:
        self.db, self.integration, self.operation = db, integration, operation
        self.http_status: int | None = None
        self.detail: str | None = None

    def __enter__(self) -> Self:
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        ok = exc is None
        detail = self.detail if ok else str(getattr(exc, "message", exc))
        self.db.add(IntegrationLog(
            business_id=self.integration.business_id, integration_id=self.integration.id,
            direction="outbound", operation=self.operation, success=ok,
            http_status=self.http_status or getattr(exc, "http_status", None),
            detail=(detail or "")[:1000] or None,
            duration_ms=int((time.perf_counter() - self.start) * 1000)))
        self.integration.last_error = None if ok else (detail or "")[:500]


def check(resp: httpx.Response, log: CallLog | None = None, what: str = "request") -> Any:
    """Raise IntegrationError for non-2xx; return parsed JSON (or None)."""
    if log:
        log.http_status = resp.status_code
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text[:300]
        err = IntegrationError(f"{what} failed with HTTP {resp.status_code}: {str(body)[:300]}")
        err.http_status = resp.status_code  # type: ignore[attr-defined]
        raise err
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def hmac_b64(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def same(a: str | None, b: str) -> bool:
    return a is not None and hmac.compare_digest(a.strip(), b)
