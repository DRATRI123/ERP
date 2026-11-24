"""Encryption at rest for integration credentials (API keys, tokens, webhook secrets).

``ERP_ENCRYPTION_KEYS`` is a list of Fernet keys (AES-128-CBC + HMAC-SHA256). The first key
encrypts; every key can decrypt, so keys rotate by prepending a new one, running
``python -m app.cli rotate-encryption`` to re-encrypt stored secrets, then removing the old key.
Generate a key with ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.errors import AppError


class EncryptionUnavailable(AppError):
    status_code = 503
    code = "encryption_not_configured"


def _fernet() -> MultiFernet:
    keys = get_settings().encryption_keys
    if not keys:
        raise EncryptionUnavailable("Set ERP_ENCRYPTION_KEYS before storing integration credentials")
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except ValueError as exc:
        raise EncryptionUnavailable("ERP_ENCRYPTION_KEYS contains an invalid key") from exc


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise EncryptionUnavailable("Stored credentials cannot be decrypted with the configured keys") from exc


def reencrypt(token: str) -> str:
    """Re-encrypt with the current primary key (key rotation)."""
    return _fernet().rotate(token.encode()).decode()
