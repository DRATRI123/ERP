"""Two-factor authentication management and account security events (with e-mail alerts)."""

from __future__ import annotations

import contextlib
import logging
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import client_ip_var, user_agent_var
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import ValidationFailed
from app.core.security import hash_token, verify_password
from app.modules.audit import service as audit
from app.modules.auth.models import LoginEvent
from app.modules.system import totp
from app.modules.system.models import RecoveryCode, SecurityEvent
from app.modules.users.models import User

log = logging.getLogger("erp.security")

ALERT_SUBJECTS = {
    "new_device_login": "New sign-in to your ERP account",
    "account_locked": "Your ERP account was locked after failed sign-ins",
    "2fa_enabled": "Two-factor authentication was turned on",
    "2fa_disabled": "Two-factor authentication was turned off",
    "recovery_codes_regenerated": "New two-factor recovery codes were generated",
    "recovery_code_used": "A two-factor recovery code was used",
    "password_changed": "Your ERP password was changed",
}


def _now() -> datetime:
    return datetime.now(UTC)


def record(db: Session, user: User, kind: str, **detail: Any) -> SecurityEvent:
    ev = SecurityEvent(user_id=user.id, kind=kind, detail={
        "ip": client_ip_var.get(), "user_agent": (user_agent_var.get() or "")[:200], **detail})
    db.add(ev)
    return ev


def is_new_device(db: Session, user: User) -> bool:
    """No successful sign-in from this IP + browser in the last 90 days (checked before the
    current sign-in is logged)."""
    ip, ua = client_ip_var.get(), (user_agent_var.get() or "")[:300] or None
    seen = db.scalar(select(LoginEvent.id).where(
        LoginEvent.user_id == user.id, LoginEvent.success.is_(True), LoginEvent.event == "login",
        LoginEvent.ip_address == ip, LoginEvent.user_agent == ua,
        LoginEvent.created_at > _now() - timedelta(days=90)).limit(1))
    return seen is None


def has_logged_in_before(db: Session, user: User) -> bool:
    return db.scalar(select(LoginEvent.id).where(
        LoginEvent.user_id == user.id, LoginEvent.success.is_(True),
        LoginEvent.event == "login").limit(1)) is not None


# ---------------------------------------------------------------------------- TOTP

def mfa_enabled(user: User) -> bool:
    return user.totp_enabled_at is not None


def _secret(user: User) -> str:
    assert user.totp_secret_encrypted
    return decrypt_json(user.totp_secret_encrypted)["secret"]


def begin_setup(db: Session, user: User, password: str) -> dict[str, str]:
    if not verify_password(user.password_hash, password):
        raise ValidationFailed("Password is incorrect", code="wrong_password")
    if mfa_enabled(user):
        raise ValidationFailed("Two-factor authentication is already on; turn it off first",
                               code="mfa_already_enabled")
    secret = totp.new_secret()
    user.totp_secret_encrypted = encrypt_json({"secret": secret})
    user.totp_last_step = None
    uri = totp.provisioning_uri(secret, user.email)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": totp.qr_svg(uri)}


def check_code(user: User, code: str) -> bool:
    """Verify a TOTP code with replay protection (marks the step used)."""
    if not user.totp_secret_encrypted:
        return False
    step = totp.verify(_secret(user), code, last_step=user.totp_last_step)
    if step is None:
        return False
    user.totp_last_step = step
    return True


def _issue_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    for c in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_token(f"{user.id}:{c}")))
    return codes


def use_recovery_code(db: Session, user: User, code: str) -> bool:
    h = hash_token(f"{user.id}:{totp.normalize_recovery_code(code)}")
    rc = db.scalar(select(RecoveryCode).where(RecoveryCode.user_id == user.id,
                                              RecoveryCode.code_hash == h,
                                              RecoveryCode.used_at.is_(None)).with_for_update())
    if rc is None:
        return False
    rc.used_at = _now()
    left = db.scalar(select(RecoveryCode.id).where(RecoveryCode.user_id == user.id,
                                                   RecoveryCode.used_at.is_(None),
                                                   RecoveryCode.id != rc.id).limit(1))
    record(db, user, "recovery_code_used", remaining=left is not None)
    return True


def enable(db: Session, user: User, code: str, *, session_id: uuid.UUID | None) -> list[str]:
    if mfa_enabled(user):
        raise ValidationFailed("Two-factor authentication is already on", code="mfa_already_enabled")
    if not user.totp_secret_encrypted:
        raise ValidationFailed("Start the setup first", code="mfa_setup_required")
    if not check_code(user, code):
        raise ValidationFailed("That code is not valid. Check the time on your phone and try again.",
                               code="invalid_mfa_code")
    user.totp_enabled_at = _now()
    codes = _issue_recovery_codes(db, user)
    from app.modules.users import service as users

    users.revoke_all_sessions(db, user.id, "mfa_enabled", except_session=session_id)
    record(db, user, "2fa_enabled")
    audit.record(db, action="user.mfa_enabled", entity_type="user", entity_id=user.id, actor_id=user.id)
    return codes


def _confirm(user: User, password: str, code: str) -> None:
    if not verify_password(user.password_hash, password):
        raise ValidationFailed("Password is incorrect", code="wrong_password")
    if not mfa_enabled(user):
        raise ValidationFailed("Two-factor authentication is not on", code="mfa_not_enabled")
    if not check_code(user, code):
        raise ValidationFailed("That code is not valid", code="invalid_mfa_code")


def disable(db: Session, user: User, *, password: str, code: str) -> None:
    _confirm(user, password, code)
    user.totp_enabled_at = None
    user.totp_secret_encrypted = None
    user.totp_last_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    record(db, user, "2fa_disabled")
    audit.record(db, action="user.mfa_disabled", entity_type="user", entity_id=user.id, actor_id=user.id)


def regenerate_codes(db: Session, user: User, *, password: str, code: str) -> list[str]:
    _confirm(user, password, code)
    codes = _issue_recovery_codes(db, user)
    record(db, user, "recovery_codes_regenerated")
    return codes


def owner_reset_mfa(db: Session, *, target: User, actor: User) -> None:
    """The owner can switch off 2FA for a member who lost their phone and recovery codes."""
    target.totp_enabled_at = None
    target.totp_secret_encrypted = None
    target.totp_last_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == target.id))
    from app.modules.users import service as users

    users.revoke_all_sessions(db, target.id, "mfa_reset")
    record(db, target, "2fa_disabled", by="owner")
    audit.record(db, action="user.mfa_reset", entity_type="user", entity_id=target.id, actor_id=actor.id)


def recovery_codes_left(db: Session, user: User) -> int:
    return len(db.scalars(select(RecoveryCode.id).where(
        RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))).all())


# ---------------------------------------------------------------------------- e-mail alerts

def _smtp_send(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if s.smtp_security == "ssl":
        conn = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ctx)
    else:
        conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        conn.starttls(context=ctx)
    try:
        if s.smtp_username:
            conn.login(s.smtp_username, s.smtp_password or "")
        conn.send_message(msg)
    finally:
        with contextlib.suppress(smtplib.SMTPException):
            conn.quit()


def _alert_body(user: User, ev: SecurityEvent) -> str:
    d = ev.detail or {}
    when = ev.created_at.strftime("%d %b %Y %H:%M UTC")
    lines = [f"Hello {user.full_name},", "", f"{ALERT_SUBJECTS.get(ev.kind, ev.kind)} — {when}."]
    if d.get("ip"):
        lines.append(f"IP address: {d['ip']}")
    if d.get("user_agent"):
        lines.append(f"Device: {d['user_agent']}")
    lines += ["", ("If this was you, no action is needed. If not, change your password now, sign "
                   "out other sessions and turn on two-factor authentication.")]
    return "\n".join(lines)


def deliver_alerts(db: Session, limit: int = 50) -> dict[str, int]:
    """Worker: e-mail pending security events (when system SMTP is configured)."""
    s = get_settings()
    if not (s.smtp_host and s.smtp_from):
        return {}
    stats = {"alerts_sent": 0, "alerts_failed": 0}
    rows = db.scalars(select(SecurityEvent).where(
        SecurityEvent.emailed_at.is_(None), SecurityEvent.email_error.is_(None),
        SecurityEvent.created_at > _now() - timedelta(days=2),
        SecurityEvent.kind.in_(list(ALERT_SUBJECTS))).order_by(SecurityEvent.created_at).limit(limit)).all()
    for ev in rows:
        user = db.get(User, ev.user_id)
        if user is None:
            continue
        try:
            _smtp_send(user.email, ALERT_SUBJECTS[ev.kind], _alert_body(user, ev))
            ev.emailed_at = _now()
            stats["alerts_sent"] += 1
        except (smtplib.SMTPException, OSError) as exc:
            ev.email_error = str(exc)[:300]
            stats["alerts_failed"] += 1
            log.warning("security alert e-mail failed: %s", exc)
        db.commit()
    return stats
