"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.context import client_ip_var, request_id_var, user_agent_var
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.session import sqlite_write_intent

API_PREFIX = "/api/v1"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
log = logging.getLogger("erp.request")


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env != "test":
        configure_logging()

    app = FastAPI(
        title="Private ERP API",
        version="0.1.0",
        description="Core business API: authentication, businesses, branches, users and "
        "permissions (Phase 1).",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        tokens = (
            request_id_var.set(rid),
            client_ip_var.set(request.client.host if request.client else None),
            user_agent_var.set(request.headers.get("user-agent")),
            sqlite_write_intent.set(request.method not in _SAFE_METHODS),
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            log.info("request", extra={
                "method": request.method, "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
            return response
        finally:
            request_id_var.reset(tokens[0])
            client_ip_var.reset(tokens[1])
            user_agent_var.reset(tokens[2])
            sqlite_write_intent.reset(tokens[3])

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
            expose_headers=["X-Request-ID"],
        )

    install_error_handlers(app)

    from app.api.routes import (
        access,
        accounting,
        audit,
        auth,
        billing,
        businesses,
        catalog,
        gst,
        health,
        inventory,
        parties,
        users,
    )

    for router in (auth.router, businesses.router, access.router, users.router,
                   catalog.router, parties.customers, parties.suppliers, inventory.router,
                   billing.router, gst.router, accounting.router,
                   audit.router, health.router):
        app.include_router(router, prefix=API_PREFIX)
    return app
