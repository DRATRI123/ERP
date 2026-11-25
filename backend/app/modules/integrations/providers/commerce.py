"""Google Workspace (OAuth 2.0: Gmail send, Drive backup) and Shopify (orders, products)."""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.modules.integrations.base import (
    Field,
    IntegrationError,
    ProviderSpec,
    check,
    hmac_b64,
    http_client,
    register,
    same,
)

# ---------------------------------------------------------------------------- Google

GOOGLE = register(ProviderSpec(
    code="google", label="Google Workspace (Gmail, Drive)", category="google",
    fields=(Field("client_id", "OAuth client ID", secret=True),
            Field("client_secret", "OAuth client secret", secret=True),
            Field("drive_folder_id", "Drive folder for backups", required=False)),
    capabilities=("send_email", "drive_backup"), oauth=True,
    docs="Google Cloud Console → APIs & Services: enable Gmail API and Google Drive API, set up the OAuth "
         "consent screen, then Credentials → Create OAuth client ID → Web application, and paste the "
         "Authorized JavaScript origin and Authorized redirect URI shown here.",
))
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/drive.file"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def google_auth_url(creds: dict[str, Any], *, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": creds["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state})


def google_exchange_code(creds: dict[str, Any], *, code: str, redirect_uri: str) -> dict[str, Any]:
    with http_client() as c:
        tok = check(c.post(TOKEN_URL, data={"code": code, "client_id": creds["client_id"],
                                            "client_secret": creds["client_secret"],
                                            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}),
                    what="Google token exchange")
    if not tok.get("refresh_token"):
        raise IntegrationError("Google did not return a refresh token; remove the app's access in "
                               "your Google account and connect again")
    return tok


def google_access_token(creds: dict[str, Any]) -> str:
    if not creds.get("refresh_token"):
        raise IntegrationError("Google is not authorised yet; complete the consent step")
    with http_client() as c:
        tok = check(c.post(TOKEN_URL, data={"client_id": creds["client_id"], "client_secret": creds["client_secret"],
                                            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token"}),
                    what="Google token refresh")
    return tok["access_token"]


def google_revoke(token: str) -> None:
    """Withdraw the ERP's access in the Google account (also removes it from 'Third-party apps')."""
    with http_client() as c:
        c.post("https://oauth2.googleapis.com/revoke", data={"token": token})


def google_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    google_access_token(creds)
    return "Google authorisation is valid"


def gmail_send(config: dict[str, Any], creds: dict[str, Any], *, to: str, subject: str, html: str,
               attachments: list[tuple[str, bytes, str]]) -> str:
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg.set_content("This message is best viewed in an HTML-capable e-mail client.")
    msg.add_alternative(html, subtype="html")
    for filename, data, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    token = google_access_token(creds)
    with http_client(headers={"Authorization": f"Bearer {token}"}) as c:
        data = check(c.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": raw}),
                     what="Gmail send")
    return data["id"]


DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_CHUNK = 8 * 1024 * 1024  # must be a multiple of 256 KiB


def drive_upload_file(config: dict[str, Any], creds: dict[str, Any], *, path: Path,
                      mime: str = "application/octet-stream") -> str:
    """Resumable upload (any size, constant memory). Returns the Drive file id."""
    meta: dict[str, Any] = {"name": path.name}
    if config.get("drive_folder_id"):
        meta["parents"] = [config["drive_folder_id"]]
    total = path.stat().st_size
    token = google_access_token(creds)
    with http_client(headers={"Authorization": f"Bearer {token}"}) as c:
        start = c.post(DRIVE_UPLOAD, params={"uploadType": "resumable"}, json=meta,
                       headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(total)})
        check(start, what="Drive upload start")
        session_url = start.headers.get("location")
        if not session_url or not session_url.startswith(DRIVE_UPLOAD):
            raise IntegrationError("Drive did not return an upload session")
        offset = 0
        with path.open("rb") as f:
            while True:
                block = f.read(DRIVE_CHUNK)
                end = offset + len(block) - 1
                rng = f"bytes {offset}-{end}/{total}" if block else f"bytes */{total}"
                resp = c.put(session_url, content=block, headers={"Content-Range": rng})
                if resp.status_code == 308:  # chunk accepted, more expected
                    offset = end + 1
                    if not block:
                        raise IntegrationError("Drive upload ended early")
                    continue
                out = check(resp, what="Drive upload")
                return out["id"]


# ---------------------------------------------------------------------------- Shopify

SHOPIFY = register(ProviderSpec(
    code="shopify", label="Shopify", category="commerce",
    fields=(Field("shop_domain", "Shop domain", help="yourstore.myshopify.com"),
            Field("warehouse_id", "ERP warehouse that ships online orders"),
            Field("branch_id", "ERP branch that invoices online orders"),
            Field("settlement_account_id", "ERP bank/clearing account for paid orders"),
            Field("shipping_gst_rate", "GST % on shipping charges", required=False),
            Field("admin_token", "Admin API access token", secret=True),
            Field("api_secret", "API secret key (webhook signatures)", secret=True)),
    capabilities=("import_orders", "import_products"), webhook=True,
    docs="Shopify admin → Apps → Develop apps: scopes read_orders, read_products; install and copy "
         "the Admin API token. Add webhooks orders/create and orders/paid with the URL shown.",
))
SHOPIFY_API = "2025-01"
_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")


def _shop(config: dict[str, Any], creds: dict[str, Any]):  # type: ignore[no-untyped-def]
    domain = config["shop_domain"].strip().lower()
    if not _DOMAIN.match(domain):  # never send the token to an arbitrary host
        raise IntegrationError("shop_domain must look like yourstore.myshopify.com")
    return http_client(f"https://{domain}/admin/api/{SHOPIFY_API}",
                       headers={"X-Shopify-Access-Token": creds["admin_token"]})


def shopify_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    with _shop(config, creds) as c:
        shop = check(c.get("/shop.json"), what="Shopify")["shop"]
    return f"Connected to {shop.get('name')} ({shop.get('currency')})"


def _pages(config: dict[str, Any], creds: dict[str, Any], path: str, key: str,
           params: dict[str, Any]) -> list[dict[str, Any]]:
    """Cursor pagination via the Link header (rel="next")."""
    out: list[dict[str, Any]] = []
    with _shop(config, creds) as c:
        url, query = path, {**params, "limit": 250}
        for _ in range(200):  # hard stop: 50k records per sync
            resp = c.get(url, params=query)
            out += check(resp, what=f"Shopify {key}")[key]
            m = re.search(r'<[^>]*[?&]page_info=([^&>]+)[^>]*>;\s*rel="next"', resp.headers.get("link", ""))
            if not m:
                break
            url, query = path, {"limit": 250, "page_info": m.group(1)}
    return out


def shopify_orders(config: dict[str, Any], creds: dict[str, Any], *, updated_since: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"status": "any"}
    if updated_since:
        params["updated_at_min"] = updated_since
    return _pages(config, creds, "/orders.json", "orders", params)


def shopify_products(config: dict[str, Any], creds: dict[str, Any]) -> list[dict[str, Any]]:
    return _pages(config, creds, "/products.json", "products", {})


def shopify_verify(creds: dict[str, Any], body: bytes, header: str | None) -> bool:
    return same(header, hmac_b64(creds["api_secret"], body))
