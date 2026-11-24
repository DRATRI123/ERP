"""Time-based one-time passwords (RFC 6238, HMAC-SHA1, 6 digits, 30 s) — the scheme every
authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password…) uses."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
ISSUER = "Private ERP"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret: str, counter: int) -> str:
    """RFC 4226."""
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return str(code).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def verify(secret: str, code: str, *, last_step: int | None, now: float | None = None,
           window: int = 1) -> int | None:
    """Return the matched time step, or None. Accepts ±``window`` steps for clock drift and
    rejects any step at or before ``last_step`` (a code can be used only once)."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - window, step + window + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(hotp(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?" + urlencode(
        {"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": STEP})


def qr_svg(uri: str) -> str:
    import io

    import segno

    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=4, xmldecl=False, svgns=True)
    return buf.getvalue().decode()


# ---------------------------------------------------------------------------- recovery codes

_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/o/1/l/i


def new_recovery_codes(n: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(n)]


def normalize_recovery_code(code: str) -> str:
    raw = code.strip().lower().replace("-", "").replace(" ", "")
    return f"{raw[:5]}-{raw[5:]}"
