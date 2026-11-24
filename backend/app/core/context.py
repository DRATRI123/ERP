"""Per-request context (request id, client IP, user agent) carried via contextvars."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent_var: ContextVar[str | None] = ContextVar("user_agent", default=None)
# Prefix of the API key making the request (recorded in the audit trail). Set by the HTTP
# middleware: context variables set inside sync dependencies do not reach the endpoint.
api_key_var: ContextVar[str | None] = ContextVar("api_key", default=None)
