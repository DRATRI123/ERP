"""The address phones outside this computer can use to reach the ERP.

* A server install: ``ERP_PUBLIC_BASE_URL`` (e.g. https://erp.example.com).
* A computer install running online.bat: the tunnel address in data/public_url.json, refreshed
  every 30 seconds while the tunnel runs (older than 2 minutes = the tunnel has stopped).
* Otherwise none: links only work on this computer (or the shop network).
"""

from __future__ import annotations

import ipaddress
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[3]
STATE = BACKEND_DIR / "data" / "public_url.json"


def is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith((".local", ".lan", ".internal", ".home")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def tunnel() -> str | None:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(data.get("seen_at", 0)) > 120:
        return None
    url = str(data.get("url") or "")
    return url if url.startswith("https://") else None


def public_address() -> dict[str, Any]:
    t = tunnel()
    if t:
        return {"url": t, "kind": "tunnel"}
    base = get_settings().public_base_url.rstrip("/")
    if not is_local(base):
        return {"url": base, "kind": "server"}
    return {"url": None, "kind": "local"}
