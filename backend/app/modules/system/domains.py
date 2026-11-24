"""Custom domains: ownership proof by DNS TXT record, Host-header allow-list, TLS gating.

A domain is served (and gets a certificate from Caddy's on-demand TLS) only once its owner has
published ``_erp-verify.<hostname>  TXT  "erp-verify=<token>"``. Verification is repeated
daily; a domain whose record stays missing is disabled after ``MAX_FAILURES`` checks.

A domain never grants access to anything: requests on it authenticate exactly like requests on
the main host. ``default_business_id`` only tells the frontend which business to open.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import service as audit
from app.modules.businesses.models import Business
from app.modules.system.models import CustomDomain, DomainStatus

VERIFY_PREFIX = "_erp-verify."
MAX_FAILURES = 3
REVERIFY_AFTER = timedelta(hours=20)
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_hostname(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationFailed("Not a valid domain name", code="invalid_domain") from exc
    try:
        ipaddress.ip_address(host)
        raise ValidationFailed("Use a domain name, not an IP address", code="invalid_domain")
    except ValueError:
        pass
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(lb) for lb in labels)
            or labels[-1].isdigit()):
        raise ValidationFailed("Not a valid domain name", code="invalid_domain")
    return host


# ---------------------------------------------------------------------------- DNS lookups

def _dns_txt(name: str) -> list[str]:
    import dns.exception
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=5.0)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as exc:
        raise LookupError(f"DNS lookup failed: {exc.__class__.__name__}") from exc
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


txt_lookup: Callable[[str], list[str]] = _dns_txt  # swapped in tests


def set_txt_lookup(fn: Callable[[str], list[str]] | None) -> None:
    global txt_lookup
    txt_lookup = fn or _dns_txt


# ---------------------------------------------------------------------------- allowed hosts cache

class _HostCache:
    """Verified domains, refreshed at most every ``ttl`` seconds (and on every change made by
    this process). Other processes pick changes up within ``ttl``."""

    def __init__(self, ttl: float = 30.0) -> None:
        self.ttl = ttl
        self._hosts: frozenset[str] = frozenset()
        self._loaded = 0.0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        self._loaded = 0.0

    def get(self) -> frozenset[str]:
        if time.monotonic() - self._loaded < self.ttl:
            return self._hosts
        with self._lock:
            if time.monotonic() - self._loaded >= self.ttl:
                from app.db.session import SessionLocal

                db = SessionLocal()
                try:
                    rows = db.scalars(select(CustomDomain.hostname)
                                      .where(CustomDomain.status == DomainStatus.ACTIVE)).all()
                finally:
                    db.close()
                self._hosts = frozenset(rows)
                self._loaded = time.monotonic()
        return self._hosts


host_cache = _HostCache()


def host_allowed(host: str) -> bool:
    """Is ``host`` (Host header, port stripped) one this server answers to?"""
    static = get_settings().allowed_hosts
    if not static:
        return True  # check disabled (development)
    host = host.lower().rstrip(".")
    if host in static or host in get_settings().internal_hosts:
        return True
    if any(h.startswith("*.") and host.endswith(h[1:]) for h in static):
        return True
    return host in host_cache.get()


def certificate_allowed(db: Session, host: str) -> bool:
    """Caddy on-demand TLS: may a certificate be obtained for ``host``? Only for configured
    hosts and verified, active custom domains — never for arbitrary names pointed at us."""
    host = host.lower().rstrip(".")
    static = get_settings().allowed_hosts
    if host in static:
        return True
    return db.scalar(select(CustomDomain.id).where(
        CustomDomain.hostname == host, CustomDomain.status == DomainStatus.ACTIVE)) is not None


# ---------------------------------------------------------------------------- management

def list_domains(db: Session) -> list[CustomDomain]:
    return list(db.scalars(select(CustomDomain).order_by(CustomDomain.hostname)))


def get_domain(db: Session, domain_id: uuid.UUID) -> CustomDomain:
    d = db.get(CustomDomain, domain_id)
    if d is None:
        raise NotFound("Domain not found")
    return d


def _check_business(db: Session, business_id: uuid.UUID | None) -> None:
    if business_id is not None and db.get(Business, business_id) is None:
        raise ValidationFailed("Business not found", code="invalid_business")


def add_domain(db: Session, *, hostname: str, default_business_id: uuid.UUID | None,
               actor_id: uuid.UUID) -> CustomDomain:
    host = normalize_hostname(hostname)
    if host in get_settings().allowed_hosts:
        raise ValidationFailed("This host is already configured in ERP_ALLOWED_HOSTS")
    if db.scalar(select(CustomDomain.id).where(CustomDomain.hostname == host)):
        raise Conflict("This domain has already been added", code="domain_exists")
    _check_business(db, default_business_id)
    d = CustomDomain(hostname=host, default_business_id=default_business_id,
                     verification_token=secrets.token_hex(16), status=DomainStatus.PENDING,
                     created_by_id=actor_id)
    db.add(d)
    db.flush()
    audit.record(db, action="domain.added", entity_type="custom_domain", entity_id=d.id,
                 actor_id=actor_id, changes={"hostname": host})
    return d


def dns_instructions(d: CustomDomain) -> dict[str, str]:
    return {"type": "TXT", "name": VERIFY_PREFIX + d.hostname,
            "value": f"erp-verify={d.verification_token}"}


def check(db: Session, d: CustomDomain, *, actor_id: uuid.UUID | None = None) -> bool:
    """Look up the TXT record and update the domain. Returns whether it is (still) verified."""
    now = datetime.now(UTC)
    d.last_checked_at = now
    expected = f"erp-verify={d.verification_token}"
    try:
        records = txt_lookup(VERIFY_PREFIX + d.hostname)
        ok = any(r.strip().strip('"') == expected for r in records)
        error = None if ok else "TXT record not found"
    except LookupError as exc:
        ok, error = False, str(exc)[:300]

    before = d.status
    if ok:
        d.consecutive_failures = 0
        d.last_error = None
        if d.status == DomainStatus.PENDING:
            d.status = DomainStatus.ACTIVE
            d.verified_at = now
    else:
        d.last_error = error
        if d.status == DomainStatus.ACTIVE:
            d.consecutive_failures += 1
            if d.consecutive_failures >= MAX_FAILURES:
                d.status = DomainStatus.DISABLED
    if d.status != before:
        audit.record(db, action=f"domain.{d.status.value}", entity_type="custom_domain",
                     entity_id=d.id, actor_id=actor_id,
                     changes={"hostname": d.hostname, "from": before.value, "reason": error})
        host_cache.invalidate()
    return ok


def update_domain(db: Session, d: CustomDomain, *, actor_id: uuid.UUID,
                  default_business_id: uuid.UUID | None | object = ...,
                  enabled: bool | None = None) -> CustomDomain:
    if default_business_id is not ...:
        _check_business(db, default_business_id)  # type: ignore[arg-type]
        d.default_business_id = default_business_id  # type: ignore[assignment]
    if enabled is False and d.status != DomainStatus.DISABLED:
        d.status = DomainStatus.DISABLED
        audit.record(db, action="domain.disabled", entity_type="custom_domain", entity_id=d.id,
                     actor_id=actor_id, changes={"hostname": d.hostname, "reason": "owner"})
    elif enabled is True and d.status == DomainStatus.DISABLED:
        # Re-enabling always requires a fresh proof of ownership.
        d.status = DomainStatus.PENDING
        d.consecutive_failures = 0
        check(db, d, actor_id=actor_id)
    host_cache.invalidate()
    return d


def delete_domain(db: Session, d: CustomDomain, *, actor_id: uuid.UUID) -> None:
    audit.record(db, action="domain.deleted", entity_type="custom_domain", entity_id=d.id,
                 actor_id=actor_id, changes={"hostname": d.hostname})
    db.delete(d)
    host_cache.invalidate()


def reverify_due(db: Session) -> dict[str, int]:
    """Worker: re-check active domains daily and pending ones hourly-ish (each loop it is due)."""
    now = datetime.now(UTC)
    due = db.scalars(select(CustomDomain).where(
        CustomDomain.status != DomainStatus.DISABLED,
        or_(CustomDomain.last_checked_at.is_(None),
            CustomDomain.last_checked_at < now - REVERIFY_AFTER,
            (CustomDomain.status == DomainStatus.PENDING)
            & (CustomDomain.last_checked_at < now - timedelta(minutes=10))))).all()
    stats = {"checked": 0, "failed": 0}
    for d in due:
        stats["checked"] += 1
        if not check(db, d):
            stats["failed"] += 1
    db.commit()
    return stats
