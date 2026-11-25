"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.core import metrics
from app.core.config import get_settings
from app.core.context import client_ip_var, request_id_var, user_agent_var
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.session import sqlite_write_intent
from app.modules.system import maintenance
from app.modules.system.domains import host_allowed

API_PREFIX = "/api/v1"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DOCS_PATH = f"{API_PREFIX}/docs"
# Internal probes: called over plain HTTP inside the Docker network by health checks, Caddy
# and Prometheus, so they skip the Host check and the HTTPS redirect (they are harmless or
# token-protected).
_PROBE_PATHS = {f"{API_PREFIX}/health", f"{API_PREFIX}/ready", f"{API_PREFIX}/metrics",
                f"{API_PREFIX}/system/tls-ask"}
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
             "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
             "https://fastapi.tiangolo.com; frame-ancestors 'none'; base-uri 'none'")
log = logging.getLogger("erp.request")


def _nul_rejected() -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {
        "code": "invalid_characters", "message": "The request contains a NUL character", "details": None}})


def _host_only(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # [IPv6]:port
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env != "test":
        configure_logging()
    if settings.env == "production":
        for problem in settings.check_production():
            logging.getLogger("erp").warning("production settings: %s", problem)

    app = FastAPI(
        title="Private ERP API",
        version="0.1.0",
        description="Private ERP: businesses, inventory, billing & GST, accounting, integrations, "
        "developer API and operations.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        path = request.url.path
        probe = path in _PROBE_PATHS
        if not probe:
            host = _host_only(request.headers.get("host", ""))
            if not await run_in_threadpool(host_allowed, host):
                return JSONResponse(status_code=421, content={"error": {
                    "code": "unknown_host", "message": "This server does not serve that host name"}})
            if settings.force_https and request.url.scheme != "https" and host not in settings.internal_hosts:
                return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
            # NUL characters are never valid business text, and PostgreSQL refuses to store or
            # compare them: reject them at the edge, identically on every database.
            if b"%00" in request.scope.get("query_string", b"") or "\x00" in path:
                return _nul_rejected()
            if request.method in ("POST", "PUT", "PATCH") and \
                    request.headers.get("content-type", "").startswith("application/json"):
                body = await request.body()
                if b"\\u0000" in body or b"\x00" in body:
                    return _nul_rejected()
            paused = await run_in_threadpool(maintenance.blocks, request.method, path)
            if paused:
                return JSONResponse(status_code=503, headers={"Retry-After": "120"}, content={"error": {
                    "code": "maintenance", "message": paused.get("message") or maintenance.DEFAULT_MESSAGE}})
        tokens = (
            request_id_var.set(rid),
            client_ip_var.set(request.client.host if request.client else None),
            user_agent_var.set(request.headers.get("user-agent")),
            sqlite_write_intent.set(request.method not in _SAFE_METHODS),
        )
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:  # unhandled: answer here so the error still carries the request id
                logging.getLogger("erp.errors").exception("Unhandled error")
                response = JSONResponse(status_code=500, content={"error": {
                    "code": "internal_error", "message": "Internal server error",
                    "details": None, "request_id": rid}})
            elapsed = time.perf_counter() - start
            h = response.headers
            h["X-Request-ID"] = rid
            h["X-Content-Type-Options"] = "nosniff"
            h["Referrer-Policy"] = "no-referrer"
            h.setdefault("Cache-Control", "no-store")
            h["Cross-Origin-Opener-Policy"] = "same-origin"
            h["Cross-Origin-Resource-Policy"] = "same-site"
            h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
            if "content-security-policy" not in h:
                if path.startswith(DOCS_PATH):
                    h["Content-Security-Policy"] = _DOCS_CSP
                else:
                    h["Content-Security-Policy"] = _API_CSP
                    h["X-Frame-Options"] = "DENY"
            if settings.force_https:
                h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            route = getattr(request.scope.get("route"), "path", None)
            if route and not route.startswith(API_PREFIX):
                route = API_PREFIX + route
            metrics.http.observe(request.method, route or "unmatched", response.status_code, elapsed)
            log.info("request", extra={
                "method": request.method, "path": path,
                "status": response.status_code,
                "duration_ms": round(elapsed * 1000, 1)})
            return response
        finally:
            request_id_var.reset(tokens[0])
            client_ip_var.reset(tokens[1])
            user_agent_var.reset(tokens[2])
            sqlite_write_intent.reset(tokens[3])

    from app.api.middleware import api_middleware

    app.middleware("http")(api_middleware)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key",
                           "X-CSRF-Token"],
            allow_credentials=settings.auth_cookie,
            expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After",
                            "Idempotent-Replayed"],
        )

    install_error_handlers(app)

    from app.api.routes import (
        access,
        accounting,
        audit,
        auth,
        automation,
        billing,
        businesses,
        catalog,
        claims,
        compliance,
        dashboard,
        developer,
        digest,
        field,
        files,
        finance,
        gst,
        gst_compliance,
        health,
        hooks,
        integrations,
        inventory,
        manufacturing,
        money,
        parties,
        payroll,
        pharmacy,
        pricing,
        shop,
        stockx,
        system,
        users,
    )

    for router in (auth.router, businesses.router, access.router, users.router,
                   catalog.router, parties.customers, parties.suppliers, inventory.router,
                   billing.router, gst.router, accounting.router, integrations.router, hooks.router,
                   developer.router, system.router, dashboard.router, files.router, automation.router, compliance.router, pricing.router, digest.router, manufacturing.router, payroll.router, claims.router, shop.router, shop.public, pharmacy.router, money.router, stockx.router, field.router, gst_compliance.router, finance.router, finance.public,
                   audit.router, health.router):
        app.include_router(router, prefix=API_PREFIX)
    return app
