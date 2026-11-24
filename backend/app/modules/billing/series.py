"""Document numbering series: per business, optionally per branch, restarting each FY."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import SPECS, DocType
from app.modules.billing.models import DocumentSeries
from app.modules.businesses.models import Branch, FinancialYear
from app.modules.numbering.service import next_number

_GST_NUMBER = re.compile(r"^[A-Za-z0-9/\-]{1,16}$")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
ALLOWED_PLACEHOLDERS = {"FY", "FYS", "BRANCH"}


def render(template: str, fy: FinancialYear, branch: Branch) -> str:
    values = {"FY": fy.code, "FYS": fy.code.replace("-", "")[2:], "BRANCH": branch.code}
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def validate_template(text: str) -> None:
    bad = set(_PLACEHOLDER.findall(text)) - ALLOWED_PLACEHOLDERS
    if bad:
        raise ValidationFailed(f"Unknown placeholders: {sorted(bad)}; use {{FY}}, {{FYS}}, {{BRANCH}}")


def list_series(db: Session, access: BusinessAccess) -> list[DocumentSeries]:
    return list(db.scalars(select(DocumentSeries).where(
        DocumentSeries.business_id == access.business.id)
        .order_by(DocumentSeries.doc_type, DocumentSeries.name)))


def get_series(db: Session, access: BusinessAccess, sid: uuid.UUID) -> DocumentSeries:
    s = db.get(DocumentSeries, sid)
    if s is None or s.business_id != access.business.id:
        raise NotFound("Numbering series not found")
    return s


def _clear_other_defaults(db: Session, s: DocumentSeries) -> None:
    for other in db.scalars(select(DocumentSeries).where(
            DocumentSeries.business_id == s.business_id, DocumentSeries.doc_type == s.doc_type,
            DocumentSeries.id != s.id, DocumentSeries.is_default.is_(True))):
        if other.branch_id == s.branch_id:
            other.is_default = False


def create_series(db: Session, access: BusinessAccess, data: dict[str, Any]) -> DocumentSeries:
    access.require("templates.manage")
    validate_template(data["prefix"])
    validate_template(data.get("suffix", ""))
    if data.get("branch_id"):
        b = db.get(Branch, data["branch_id"])
        if b is None or b.business_id != access.business.id:
            raise ValidationFailed("Branch not found in this business")
    if db.scalar(select(DocumentSeries.id).where(
            DocumentSeries.business_id == access.business.id,
            DocumentSeries.doc_type == data["doc_type"], DocumentSeries.name == data["name"])):
        raise Conflict("A series with this name already exists for this document type")
    s = DocumentSeries(business_id=access.business.id, **data)
    db.add(s)
    db.flush()
    if s.is_default:
        _clear_other_defaults(db, s)
    audit.record(db, action="series.created", entity_type="document_series", entity_id=s.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={k: str(v) for k, v in data.items()})
    return s


def update_series(db: Session, access: BusinessAccess, s: DocumentSeries,
                  data: dict[str, Any]) -> DocumentSeries:
    access.require("templates.manage")
    for k in ("prefix", "suffix"):
        if k in data:
            validate_template(data[k])
    for k, v in data.items():
        setattr(s, k, v)
    if s.is_default:
        _clear_other_defaults(db, s)
    audit.record(db, action="series.updated", entity_type="document_series", entity_id=s.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={k: str(v) for k, v in data.items()})
    return s


def resolve(db: Session, business_id: uuid.UUID, doc_type: DocType, branch_id: uuid.UUID,
            series_id: uuid.UUID | None) -> DocumentSeries:
    if series_id is not None:
        s = db.get(DocumentSeries, series_id)
        if s is None or s.business_id != business_id or s.doc_type != doc_type or not s.is_active:
            raise ValidationFailed("Numbering series is not valid for this document")
        if s.branch_id is not None and s.branch_id != branch_id:
            raise ValidationFailed("This numbering series belongs to another branch")
        return s
    candidates = list(db.scalars(select(DocumentSeries).where(
        DocumentSeries.business_id == business_id, DocumentSeries.doc_type == doc_type,
        DocumentSeries.is_default.is_(True), DocumentSeries.is_active.is_(True))))
    for s in candidates:  # branch-specific default wins
        if s.branch_id == branch_id:
            return s
    for s in candidates:
        if s.branch_id is None:
            return s
    # First use: create the built-in default, e.g. INV/2026-27/0001. Two first documents
    # finalized at once may race here; the loser re-reads the winner's row.
    try:
        with db.begin_nested():
            s = DocumentSeries(business_id=business_id, doc_type=doc_type, name="Default",
                               prefix=f"{SPECS[doc_type].prefix}/{{FY}}/", suffix="",
                               padding=4, is_default=True)
            db.add(s)
        return s
    except IntegrityError:
        found = db.scalar(select(DocumentSeries).where(
            DocumentSeries.business_id == business_id, DocumentSeries.doc_type == doc_type,
            DocumentSeries.name == "Default"))
        assert found is not None
        return found


def allocate(db: Session, series: DocumentSeries, fy: FinancialYear, branch: Branch,
             *, gst_rules: bool) -> str:
    prefix = render(series.prefix, fy, branch)
    suffix = render(series.suffix, fy, branch)
    number = next_number(db, series.business_id, f"doc:{series.id}/{fy.code}", prefix=prefix,
                         padding=series.padding) + suffix
    if gst_rules and not _GST_NUMBER.match(number):
        raise ValidationFailed(
            f"Number {number} breaks GST rules (max 16 characters: letters, digits, '-' and '/'). "
            "Shorten the series prefix.")
    return number
