"""Communication providers: SMTP e-mail, WhatsApp Business Cloud API, MSG91 SMS (DLT flows)."""

from __future__ import annotations

import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.modules.integrations.base import (
    Field,
    IntegrationError,
    ProviderSpec,
    check,
    hmac_hex,
    http_client,
    register,
    same,
)

# ---------------------------------------------------------------------------- SMTP

SMTP = register(ProviderSpec(
    code="smtp", label="E-mail (SMTP)", category="communication",
    fields=(Field("host", "SMTP host", help="smtp.gmail.com, smtp.office365.com, smtp.zoho.in…"),
            Field("port", "Port", help="587 (STARTTLS) or 465 (SSL)"),
            Field("security", "Security", help="starttls | ssl"),
            Field("from_email", "From address"), Field("from_name", "From name", required=False),
            Field("username", "Username"),
            Field("password", "Password / app password", secret=True,
                  help="For Gmail use an App Password (Google account → Security)")),
    capabilities=("send_email",),
    docs="Works with Gmail/Google Workspace (app password), Microsoft 365, Zoho Mail and any SMTP server.",
))

# Swappable in tests; real smtplib classes otherwise.
smtp_classes: dict[str, Any] = {"ssl": smtplib.SMTP_SSL, "starttls": smtplib.SMTP}


def _smtp(config: dict[str, Any], creds: dict[str, Any]):  # type: ignore[no-untyped-def]
    security = config.get("security", "starttls")
    if security not in smtp_classes:
        raise IntegrationError("security must be starttls or ssl (plain SMTP is not allowed)")
    ctx = ssl.create_default_context()
    port = int(config.get("port") or (465 if security == "ssl" else 587))
    try:
        if security == "ssl":
            conn = smtp_classes["ssl"](config["host"], port, timeout=20, context=ctx)
        else:
            conn = smtp_classes["starttls"](config["host"], port, timeout=20)
            conn.starttls(context=ctx)
        conn.login(config["username"], creds["password"])
    except (smtplib.SMTPException, OSError) as exc:
        raise IntegrationError(f"SMTP connection failed: {exc}") from exc
    return conn


def smtp_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    conn = _smtp(config, creds)
    conn.noop()
    conn.quit()
    return f"Logged in to {config['host']} as {config['username']}"


def smtp_send(config: dict[str, Any], creds: dict[str, Any], *, to: str, subject: str, html: str,
              attachments: list[tuple[str, bytes, str]]) -> str:
    msg = EmailMessage()
    sender = config["from_email"]
    msg["From"] = f'{config["from_name"]} <{sender}>' if config.get("from_name") else sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This message is best viewed in an HTML-capable e-mail client.")
    msg.add_alternative(html, subtype="html")
    for filename, data, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    conn = _smtp(config, creds)
    try:
        refused = conn.send_message(msg)
    except smtplib.SMTPException as exc:
        raise IntegrationError(f"SMTP send failed: {exc}") from exc
    finally:
        try:
            conn.quit()
        except smtplib.SMTPException:
            pass
    if refused:
        raise IntegrationError(f"Recipient refused: {refused}")
    return msg.get("Message-ID") or ""


# ---------------------------------------------------------------------------- WhatsApp Cloud API

WHATSAPP = register(ProviderSpec(
    code="whatsapp_cloud", label="WhatsApp Business (Cloud API)", category="communication",
    fields=(Field("phone_number_id", "Phone number ID"),
            Field("template_name", "Approved template for documents",
                  help="A Utility template with a DOCUMENT header and body variables {{1}} name, "
                       "{{2}} document number, {{3}} amount"),
            Field("language", "Template language", help="e.g. en or en_US"),
            Field("access_token", "Permanent access token", secret=True),
            Field("app_secret", "App secret (for webhook signatures)", secret=True),
            Field("verify_token", "Webhook verify token (any random string)", secret=True)),
    capabilities=("send_whatsapp", "delivery_status"), webhook=True,
    docs="Meta Business Manager → WhatsApp → API setup. Business-initiated messages must use "
         "an approved template; Meta charges per conversation.",
))
GRAPH = "https://graph.facebook.com/v21.0"


def _wa(creds: dict[str, Any]):  # type: ignore[no-untyped-def]
    return http_client(GRAPH, headers={"Authorization": f"Bearer {creds['access_token']}"})


def whatsapp_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    with _wa(creds) as c:
        data = check(c.get(f"/{config['phone_number_id']}",
                           params={"fields": "display_phone_number,verified_name"}), what="WhatsApp")
    return f"Connected to {data.get('verified_name')} ({data.get('display_phone_number')})"


def whatsapp_send_document(config: dict[str, Any], creds: dict[str, Any], *, to: str, pdf: bytes,
                           filename: str, body_params: list[str]) -> str:
    pid = config["phone_number_id"]
    with _wa(creds) as c:
        media = check(c.post(f"/{pid}/media", data={"messaging_product": "whatsapp", "type": "application/pdf"},
                             files={"file": (filename, pdf, "application/pdf")}), what="WhatsApp media upload")
        payload = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": config["template_name"], "language": {"code": config["language"]},
                         "components": [
                             {"type": "header", "parameters": [
                                 {"type": "document", "document": {"id": media["id"], "filename": filename}}]},
                             {"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]}]},
        }
        sent = check(c.post(f"/{pid}/messages", json=payload), what="WhatsApp send")
    return sent["messages"][0]["id"]


def whatsapp_verify_signature(creds: dict[str, Any], body: bytes, header: str | None) -> bool:
    return same(header, "sha256=" + hmac_hex(creds["app_secret"], body))


def whatsapp_statuses(body: bytes) -> list[tuple[str, str, str | None]]:
    """(message id, status, error) from a webhook notification."""
    out = []
    data = json.loads(body)
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                err = st.get("errors", [{}])[0].get("title") if st.get("errors") else None
                out.append((st["id"], st["status"], err))
    return out


# ---------------------------------------------------------------------------- MSG91 (SMS)

MSG91 = register(ProviderSpec(
    code="msg91", label="SMS (MSG91)", category="communication",
    fields=(Field("invoice_flow_id", "Flow ID for invoice messages",
                  help="DLT-approved flow with variables: name, number, amount, link"),
            Field("reminder_flow_id", "Flow ID for payment reminders", required=False),
            Field("auth_key", "Auth key", secret=True)),
    capabilities=("send_sms",),
    docs="India requires DLT-registered templates; create the flow in MSG91 with the template ID.",
))
MSG91_URL = "https://control.msg91.com/api/v5/flow"


def msg91_send(config: dict[str, Any], creds: dict[str, Any], *, to: str, flow_id: str,
               variables: dict[str, str]) -> str:
    mobile = "".join(ch for ch in to if ch.isdigit())
    if len(mobile) == 10:
        mobile = "91" + mobile
    with http_client(headers={"authkey": creds["auth_key"], "accept": "application/json"}) as c:
        data = check(c.post(MSG91_URL, json={"template_id": flow_id, "short_url": "0",
                                             "recipients": [{"mobiles": mobile, **variables}]}),
                     what="MSG91 send")
    if not isinstance(data, dict) or data.get("type") != "success":
        raise IntegrationError(f"MSG91 refused the message: {data}")
    return str(data.get("message", ""))


def msg91_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    # MSG91 has no side-effect-free credential check; the test sends a real SMS (see service).
    return "Configuration saved; use 'send test SMS' with your own number to verify delivery"
