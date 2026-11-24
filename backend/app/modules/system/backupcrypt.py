"""Streaming authenticated encryption for backup files (AES-256-GCM in chunks).

File layout::

    b"ERPBK1\\n" | u32 header length | header JSON | chunk*

    chunk = u32 ciphertext length | ciphertext (plaintext ≤ 1 MiB + 16-byte GCM tag)

Each chunk's nonce is ``prefix(8 random bytes) || counter(u32)`` and its associated data is
``header || counter || final-flag``. The header (format, key id, creation time, source) is
therefore authenticated, chunks cannot be reordered, dropped or duplicated, and a truncated file
is detected because the last chunk must carry the final flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ERPBK1\n"
CHUNK = 1024 * 1024
_U32 = struct.Struct(">I")


class BackupCryptoError(Exception):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


def parse_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw.strip() + "=" * (-len(raw.strip()) % 4))
    except (ValueError, TypeError) as exc:
        raise BackupCryptoError("ERP_BACKUP_KEY is not valid base64") from exc
    if len(key) != 32:
        raise BackupCryptoError("ERP_BACKUP_KEY must decode to 32 bytes (AES-256)")
    return key


def key_id(key: bytes) -> str:
    """Public fingerprint, so a restore with the wrong key fails with a clear message."""
    return hashlib.sha256(b"erp-backup-key-id" + key).hexdigest()[:16]


def _aad(header: bytes, counter: int, final: bool) -> bytes:
    return header + _U32.pack(counter) + (b"\x01" if final else b"\x00")


def encrypt_stream(src: BinaryIO, dst: BinaryIO, key: bytes, meta: dict[str, Any]) -> None:
    header = json.dumps({**meta, "format": 1, "cipher": "AES-256-GCM", "chunk": CHUNK,
                         "key_id": key_id(key), "nonce_prefix": os.urandom(8).hex()},
                        sort_keys=True, separators=(",", ":")).encode()
    prefix = bytes.fromhex(json.loads(header)["nonce_prefix"])
    aes = AESGCM(key)
    dst.write(MAGIC + _U32.pack(len(header)) + header)
    counter = 0
    block = src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK)
        final = not nxt
        ct = aes.encrypt(prefix + _U32.pack(counter), block, _aad(header, counter, final))
        dst.write(_U32.pack(len(ct)) + ct)
        if final:
            return
        counter += 1
        if counter >= 2**32 - 1:
            raise BackupCryptoError("Backup too large for one file")
        block = nxt


def read_header(src: BinaryIO) -> tuple[bytes, dict[str, Any]]:
    if src.read(len(MAGIC)) != MAGIC:
        raise BackupCryptoError("Not an ERP backup file")
    (n,) = _U32.unpack(src.read(4))
    if n > 64 * 1024:
        raise BackupCryptoError("Corrupt backup header")
    raw = src.read(n)
    try:
        return raw, json.loads(raw)
    except ValueError as exc:
        raise BackupCryptoError("Corrupt backup header") from exc


def decrypt_chunks(src: BinaryIO, key: bytes) -> Iterator[bytes]:
    raw, header = read_header(src)
    if header.get("key_id") != key_id(key):
        raise BackupCryptoError("This backup was encrypted with a different key")
    prefix = bytes.fromhex(header["nonce_prefix"])
    aes = AESGCM(key)
    counter = 0
    while True:
        lb = src.read(4)
        if len(lb) < 4:
            raise BackupCryptoError("Backup file is truncated")
        (n,) = _U32.unpack(lb)
        if n > header["chunk"] + 16:
            raise BackupCryptoError("Corrupt backup chunk")
        ct = src.read(n)
        nonce = prefix + _U32.pack(counter)
        try:  # the final flag is unknown until we try: a non-final chunk fails with final=True
            yield aes.decrypt(nonce, ct, _aad(raw, counter, False))
        except InvalidTag:
            try:
                pt = aes.decrypt(nonce, ct, _aad(raw, counter, True))
            except InvalidTag as exc:
                raise BackupCryptoError("Backup file is corrupt or was modified") from exc
            if src.read(1):
                raise BackupCryptoError("Unexpected data after the end of the backup")
            yield pt
            return
        counter += 1


def encrypt_file(src: Path, dst: Path, key: bytes, meta: dict[str, Any]) -> None:
    with src.open("rb") as fi, dst.open("wb") as fo:
        encrypt_stream(fi, fo, key, meta)
        fo.flush()
        os.fsync(fo.fileno())


def decrypt_file(src: Path, dst: Path, key: bytes) -> dict[str, Any]:
    with src.open("rb") as fi:
        _, header = read_header(fi)
        fi.seek(0)
        with dst.open("wb") as fo:
            for block in decrypt_chunks(fi, key):
                fo.write(block)
    return header


def file_header(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return read_header(f)[1]
