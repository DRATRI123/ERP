"""Structured API errors.

Every error response has the shape::

    {"error": {"code": "not_found", "message": "...", "details": ..., "request_id": "..."}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import request_id_var

log = logging.getLogger("erp.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers
        if code:
            self.code = code


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class TooManyRequests(AppError):
    status_code = 429
    code = "too_many_requests"


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id_var.get(),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        request.state.error_code = exc.code
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body("validation_failed", "Request validation failed", details), status_code=422
        )

    from sqlalchemy.exc import OperationalError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    def _busy(exc: Exception) -> bool:
        return isinstance(exc, PoolTimeout) or "database is locked" in str(exc) or "deadlock detected" in str(exc)

    @app.exception_handler(OperationalError)
    async def _db_operational(request: Request, exc: OperationalError) -> JSONResponse:
        if _busy(exc):
            # Overload, not a bug: the transaction was rolled back completely, so retrying is safe.
            log.warning("database busy: %s", str(exc).splitlines()[0][:200])
            request.state.error_code = "database_busy"
            return JSONResponse(_body("database_busy", "The server is busy. Please try again in a moment."),
                                status_code=503, headers={"Retry-After": "2"})
        log.exception("Database error", exc_info=exc)
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)

    @app.exception_handler(PoolTimeout)
    async def _pool_timeout(request: Request, exc: PoolTimeout) -> JSONResponse:
        log.warning("database connection pool exhausted")
        return JSONResponse(_body("database_busy", "The server is busy. Please try again in a moment."),
                            status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)
