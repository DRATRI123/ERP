"""Invoice printing: template configuration, HTML rendering and PDF output.

* HTML is rendered with Jinja2 (autoescaped — every customer-supplied value is escaped).
* Paper: A4, A5, A6, 58 mm and 80 mm thermal, or custom dimensions. Browser printing uses the
  same HTML (``@page`` sets the size), so print preview and PDF match.
* Pages carry a Content-Security-Policy that forbids scripts and all network access (only
  embedded data-URL images and inline styles), in the browser and in the PDF renderer alike.
* PDF uses headless Chromium's built-in print-to-PDF. Configure ``ERP_PDF_CHROMIUM_PATH``.
"""

from __future__ import annotations

import base64
import enum
import io
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import segno
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.billing.doctypes import SPECS, DocType
from app.modules.billing.models import Document, TaxMode
from app.modules.gst.gstin import STATE_CODES
from app.modules.gst.tax import ZERO
from app.modules.gst.words import rupees_in_words

_DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")
MAX_IMAGE_CHARS = 400_000  # ~300 KB image


class Paper(enum.StrEnum):
    A4 = "a4"
    A5 = "a5"
    A6 = "a6"
    THERMAL_58 = "thermal_58"
    THERMAL_80 = "thermal_80"
    CUSTOM = "custom"


PAPER_MM = {Paper.A4: (210, 297), Paper.A5: (148, 210), Paper.A6: (105, 148),
            Paper.THERMAL_58: (58, None), Paper.THERMAL_80: (80, None)}

COLUMNS = ("sno", "description", "hsn", "batch", "expiry", "qty", "unit", "mrp", "rate",
           "discount", "taxable", "gst_rate", "tax", "amount")


class TemplateConfig(BaseModel):
    """Everything the owner can customise on a printed document."""

    paper: Paper = Paper.A4
    width_mm: int | None = Field(None, ge=40, le=500)
    height_mm: int | None = Field(None, ge=40, le=1000)
    landscape: bool = False
    margin_mm: int = Field(10, ge=0, le=40)
    # Placed inside the page's CSS, so only font-name characters are allowed.
    font_family: str = Field("Helvetica, Arial, sans-serif", max_length=120,
                             pattern=r"^[A-Za-z0-9 ,'\-]+$")
    font_size_pt: float = Field(9.5, ge=6, le=16)
    primary_color: str = Field("#1f3a5f", pattern=r"^#[0-9a-fA-F]{6}$")
    text_color: str = Field("#111111", pattern=r"^#[0-9a-fA-F]{6}$")
    background_color: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    title: str | None = Field(None, max_length=60, description="Overrides e.g. 'Tax Invoice'")
    header_text: str | None = Field(None, max_length=500)
    footer_text: str | None = Field(None, max_length=500)
    terms_text: str | None = Field(None, max_length=3000)
    bank_details: str | None = Field(None, max_length=500)
    signature_label: str = Field("Authorised Signatory", max_length=60)
    watermark_text: str | None = Field(None, max_length=40)
    columns: list[str] = Field(default_factory=lambda: [
        "sno", "description", "hsn", "qty", "unit", "rate", "discount", "taxable", "gst_rate",
        "tax", "amount"])
    show_logo: bool = True
    show_tax_summary: bool = True
    show_amount_in_words: bool = True
    show_upi_qr: bool = False
    upi_id: str | None = Field(None, pattern=r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z]{2,64}$")
    upi_name: str | None = Field(None, max_length=60)
    show_signature: bool = True
    print_custom_fields: list[str] = Field(default_factory=list)
    copy_labels: list[str] = Field(default_factory=lambda: ["Original for Recipient"])
    logo_data_url: str | None = None
    signature_data_url: str | None = None
    stamp_data_url: str | None = None

    @field_validator("columns")
    @classmethod
    def _columns(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in COLUMNS]
        if bad:
            raise ValueError(f"Unknown columns {bad}; choose from {list(COLUMNS)}")
        if "description" not in v or "amount" not in v:
            raise ValueError("Columns must include description and amount")
        return v

    @field_validator("copy_labels")
    @classmethod
    def _copies(cls, v: list[str]) -> list[str]:
        if not 1 <= len(v) <= 5 or any(len(x) > 60 for x in v):
            raise ValueError("Between 1 and 5 copy labels of up to 60 characters")
        return v

    @field_validator("logo_data_url", "signature_data_url", "stamp_data_url")
    @classmethod
    def _image(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > MAX_IMAGE_CHARS or not _DATA_URL.match(v):
            raise ValueError("Images must be PNG, JPEG or WebP data URLs of at most ~300 KB")
        return v

    def page_css(self) -> str:
        if self.paper == Paper.CUSTOM:
            w, h = self.width_mm or 210, self.height_mm
        else:
            w, h = PAPER_MM[self.paper]
        if self.landscape and h:
            w, h = h, w
        return f"{w}mm {h}mm" if h else f"{w}mm auto"

    @property
    def thermal(self) -> bool:
        return self.paper in (Paper.THERMAL_58, Paper.THERMAL_80) or (
            self.paper == Paper.CUSTOM and (self.width_mm or 210) < 90)


_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True,
)


def money(v: Decimal | None) -> str:
    """Indian digit grouping: 12,34,567.89"""
    if v is None:
        return ""
    neg = v < 0
    s = f"{abs(v):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        whole = f"{head},{tail}"
    return f"{'-' if neg else ''}{whole}.{frac}"


def qty(v: Decimal) -> str:
    """5.000 -> "5", 1.250 -> "1.25", 100 -> "100" (never scientific notation)."""
    return f"{v.normalize():f}"


_env.filters["money"] = money
_env.filters["qty"] = qty


def _qr_data_url(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=4, border=1)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def upi_link(upi_id: str, name: str | None, amount: Decimal, note: str) -> str:
    parts = [f"pa={quote(upi_id)}", f"am={amount:.2f}", "cu=INR", f"tn={quote(note)}"]
    if name:
        parts.insert(1, f"pn={quote(name)}")
    return "upi://pay?" + "&".join(parts)


def title_for(doc: Document) -> str:
    if doc.doc_type == DocType.SALES_INVOICE and doc.tax_mode == TaxMode.BILL_OF_SUPPLY:
        return "Bill of Supply"
    if doc.doc_type == DocType.SALES_INVOICE and (doc.party_snapshot or {}).get("gst_type") == "overseas":
        return "Export Invoice"
    return SPECS[doc.doc_type].label


def tax_summary(doc: Document) -> list[dict[str, Any]]:
    rows: dict[tuple, dict[str, Any]] = {}
    for ln in doc.lines:
        key = (ln.hsn_sac or "", ln.gst_rate)
        r = rows.setdefault(key, {"hsn": key[0], "rate": key[1], "taxable": ZERO, "cgst": ZERO,
                                  "sgst": ZERO, "igst": ZERO, "cess": ZERO})
        r["taxable"] += ln.taxable_value
        r["cgst"] += ln.cgst_amount
        r["sgst"] += ln.sgst_amount
        r["igst"] += ln.igst_amount
        r["cess"] += ln.cess_amount
    return [rows[k] for k in sorted(rows)]


def render_html(doc: Document, config: TemplateConfig, *, business_name: str) -> str:
    upi_qr = None
    if config.show_upi_qr and config.upi_id and doc.balance_due > 0:
        upi_qr = _qr_data_url(upi_link(config.upi_id, config.upi_name or business_name,
                                       doc.balance_due, doc.number or "Invoice"))
    irn_qr = _qr_data_url(doc.irn_signed_qr) if doc.irn_signed_qr else None
    template = _env.get_template("thermal.html.j2" if config.thermal else "document.html.j2")
    return template.render(
        doc=doc, cfg=config, title=config.title or title_for(doc), page_size=config.page_css(),
        seller=doc.seller_snapshot or {"name": business_name}, buyer=doc.party_snapshot or {},
        tax_rows=tax_summary(doc), words=rupees_in_words(doc.grand_total), upi_qr=upi_qr,
        irn_qr=irn_qr, sgst_label="UTGST" if doc.is_utgst else "SGST",
        pos_name=STATE_CODES.get(doc.place_of_supply or "", "Outside India"
                                 if doc.place_of_supply == "96" else ""),
        draft=doc.status.value == "draft", cancelled=doc.status.value == "cancelled",
        custom={k: doc.custom_fields.get(k) for k in config.print_custom_fields
                if (doc.custom_fields or {}).get(k) not in (None, "")},
    )


def combine_html(pages: list[str]) -> str:
    """Join rendered documents that share one template into a single printable HTML: one head
    (styles, @page size), each document starting on a new page. One Chromium run then prints
    them all — the browser start-up (~1 s) is paid once instead of per document."""
    if len(pages) == 1:
        return pages[0]
    head = pages[0].split("<body>", 1)[0]
    bodies = [p.split("<body>", 1)[1].rsplit("</body>", 1)[0] for p in pages]
    joined = "".join(f'<div style="page-break-before: always">{b}</div>' if i else f"<div>{b}</div>"
                     for i, b in enumerate(bodies))
    return f"{head}<body>{joined}</body></html>"


class PdfUnavailable(AppError):
    status_code = 503
    code = "pdf_unavailable"


def chromium_path() -> str | None:
    configured = get_settings().pdf_chromium_path
    if configured:
        return configured if Path(configured).exists() else None
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(html: str) -> bytes:
    exe = chromium_path()
    if exe is None:
        raise PdfUnavailable("PDF rendering is not configured on this server (set "
                             "ERP_PDF_CHROMIUM_PATH). Use the HTML print view meanwhile.")
    with tempfile.TemporaryDirectory(prefix="erp-pdf-") as tmp:
        src, out = Path(tmp) / "doc.html", Path(tmp) / "doc.pdf"
        src.write_text(html, encoding="utf-8")
        args = [exe, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                "--disable-background-networking", "--disable-extensions",
                f"--user-data-dir={tmp}/profile",
                f"--print-to-pdf={out}", src.as_uri()]
        if get_settings().pdf_no_sandbox:
            args.insert(1, "--no-sandbox")
        try:
            subprocess.run(args, capture_output=True, timeout=60, check=False)
        except subprocess.TimeoutExpired as exc:
            raise PdfUnavailable("PDF rendering timed out") from exc
        if not out.exists() or out.stat().st_size == 0:
            raise PdfUnavailable("PDF rendering failed on this server")
        return out.read_bytes()
