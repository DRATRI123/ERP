"""API middleware: key attribution, idempotency keys, API request logging, rate-limit headers."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.context import api_key_var, request_id_var
from app.core.security import decode_access_token, hash_token
from app.db.session import SessionLocal
from app.modules.developer.models import ApiKey, ApiRequestLog, IdempotencyRecord

IDEMPOTENCY_TTL = timedelta(hours=24)
MAX_STORED_BODY = 1_000_000


def key_prefix_of(auth: str | None) -> str | None:
    """Key prefix from 'Bearer erp_<prefix>_…' or from an API access token's kid claim."""
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if token.startswith("erp_"):
        parts = token.split("_")
        return parts[1] if len(parts) >= 3 else None
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    return claims.get("kid") if claims.get("typ") == "api" else None


def _identity(auth: str | None) -> str | None:
    """Verified identity for idempotency scoping (never trust an unverified token here:
    it would let one caller replay another's stored response)."""
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    db = SessionLocal()
    try:
        if token.startswith("erp_"):
            key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(token)))
            return f"key:{key.client_id}" if key else None
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
            if claims.get("typ") == "api":
                from app.core.config import get_settings

                c = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], issuer="erp")
                return f"key:{c['sub']}"
            return f"user:{decode_access_token(token)['sub']}"
        except jwt.PyJWTError:
            return None
    finally:
        db.close()


def _begin(identity: str, key: str, method: str, path: str, digest: str) -> tuple[str, IdempotencyRecord | None]:
    db = SessionLocal()
    try:
        db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < datetime.now(UTC) - IDEMPOTENCY_TTL))
        existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.identity == identity,
                                                             IdempotencyRecord.key == key))
        if existing is not None:
            db.commit()
            if existing.request_hash != digest:
                return "mismatch", existing
            return ("replay" if existing.completed else "in_flight"), existing
        db.add(IdempotencyRecord(identity=identity, key=key, method=method, path=path[:300], request_hash=digest))
        try:
            db.commit()
        except IntegrityError:  # a concurrent duplicate won the race
            db.rollback()
            return "in_flight", None
        return "new", None
    finally:
        db.close()


def _finish(identity: str, key: str, status: int, content_type: str | None, body: bytes) -> None:
    db = SessionLocal()
    try:
        rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.identity == identity,
                                                        IdempotencyRecord.key == key))
        if rec is None:
            return
        if status >= 500 or len(body) > MAX_STORED_BODY:
            db.delete(rec)  # server errors are retryable with the same key
        else:
            rec.completed, rec.status_code, rec.content_type = True, status, content_type
            rec.response_body = body.decode("utf-8", errors="replace")
        db.commit()
    finally:
        db.close()


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": None,
                                   "request_id": request_id_var.get()}}, status_code=status)


def _log_api(request: Request, status: int, duration_ms: int) -> None:
    client_id = getattr(request.state, "api_client_id", None)
    if client_id is None:
        return
    db = SessionLocal()
    try:
        db.add(ApiRequestLog(client_id=client_id, key_prefix=getattr(request.state, "api_key_prefix", None),
                             method=request.method, path=request.url.path[:300], status=status,
                             duration_ms=duration_ms, ip_address=request.client.host if request.client else None,
                             request_id=request_id_var.get(), error_code=getattr(request.state, "error_code", None)))
        db.commit()
    finally:
        db.close()


async def api_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    auth = request.headers.get("authorization")
    token = api_key_var.set(key_prefix_of(auth))
    start = time.perf_counter()
    try:
        idem_key = request.headers.get("idempotency-key")
        identity = None
        if idem_key and request.method == "POST":
            if not (1 <= len(idem_key) <= 100):
                return _error(422, "invalid_idempotency_key", "Idempotency-Key must be 1–100 characters")
            identity = await run_in_threadpool(_identity, auth)
        if identity:
            body = await request.body()
            digest = hashlib.sha256(request.method.encode() + request.url.path.encode() + b"\n" + body).hexdigest()
            state, rec = await run_in_threadpool(_begin, identity, idem_key, request.method, request.url.path, digest)
            if state == "mismatch":
                return _error(422, "idempotency_key_reused", "This Idempotency-Key was used with a different request")
            if state == "in_flight":
                return _error(409, "idempotency_in_progress", "A request with this Idempotency-Key is still running")
            if state == "replay":
                assert rec is not None
                return Response(rec.response_body or "", status_code=rec.status_code or 200,
                                media_type=rec.content_type, headers={"Idempotent-Replayed": "true"})
        response = await call_next(request)
        if identity:
            chunks = [c async for c in response.body_iterator]
            payload = b"".join(chunks)
            await run_in_threadpool(_finish, identity, idem_key, response.status_code,
                                    response.headers.get("content-type"), payload)
            response = Response(payload, status_code=response.status_code, headers=dict(response.headers),
                                media_type=response.media_type)
        limits = getattr(request.state, "rate_limit", None)
        if limits:
            response.headers["X-RateLimit-Limit"], response.headers["X-RateLimit-Remaining"] = map(str, limits)
        if getattr(request.state, "api_client_id", None) is not None:  # API calls only
            await run_in_threadpool(_log_api, request, response.status_code,
                                    int((time.perf_counter() - start) * 1000))
        return response
    finally:
        api_key_var.reset(token)

