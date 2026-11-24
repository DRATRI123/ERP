"""Invoice template storage and resolution."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import InvoiceTemplate
from app.modules.billing.printing import TemplateConfig
from app.modules.businesses.models import Branch


def list_templates(db: Session, access: BusinessAccess) -> list[InvoiceTemplate]:
    return list(db.scalars(select(InvoiceTemplate).where(
        InvoiceTemplate.business_id == access.business.id).order_by(InvoiceTemplate.name)))


def get_template(db: Session, access: BusinessAccess, tid: uuid.UUID) -> InvoiceTemplate:
    t = db.get(InvoiceTemplate, tid)
    if t is None or t.business_id != access.business.id:
        raise NotFound("Template not found")
    return t


def _check_branch(db: Session, access: BusinessAccess, branch_id: uuid.UUID | None) -> None:
    if branch_id is None:
        return
    b = db.get(Branch, branch_id)
    if b is None or b.business_id != access.business.id:
        raise ValidationFailed("Branch not found in this business")


def _clear_defaults(db: Session, t: InvoiceTemplate) -> None:
    """Only one default per (branch, document-type set)."""
    for other in db.scalars(select(InvoiceTemplate).where(
            InvoiceTemplate.business_id == t.business_id, InvoiceTemplate.id != t.id,
            InvoiceTemplate.is_default.is_(True))):
        if other.branch_id == t.branch_id and set(other.doc_types) == set(t.doc_types):
            other.is_default = False


def save_template(db: Session, access: BusinessAccess, t: InvoiceTemplate | None,
                  data: dict[str, Any]) -> InvoiceTemplate:
    access.require("templates.manage")
    if "config" in data:
        data["config"] = TemplateConfig.model_validate(data["config"]).model_dump(mode="json")
    if "doc_types" in data:
        data["doc_types"] = sorted({DocType(x).value for x in data["doc_types"]})
    _check_branch(db, access, data.get("branch_id"))
    created = t is None
    if t is None:
        t = InvoiceTemplate(business_id=access.business.id, **data)
        db.add(t)
    else:
        for k, v in data.items():
            setattr(t, k, v)
    db.flush()
    if t.is_default:
        _clear_defaults(db, t)
    audit.record(db, action="template.created" if created else "template.updated",
                 entity_type="invoice_template", entity_id=t.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": t.name, "doc_types": t.doc_types})
    return t


def resolve(db: Session, business_id: uuid.UUID, branch_id: uuid.UUID,
            doc_type: DocType) -> TemplateConfig:
    """Most specific active template: branch+type, business+type, branch, business, built-in."""
    rows = list(db.scalars(select(InvoiceTemplate).where(
        InvoiceTemplate.business_id == business_id, InvoiceTemplate.is_active.is_(True),
        InvoiceTemplate.is_default.is_(True))))

    def score(t: InvoiceTemplate) -> int | None:
        if t.branch_id not in (None, branch_id):
            return None
        if t.doc_types and doc_type.value not in t.doc_types:
            return None
        return (2 if t.doc_types else 0) + (1 if t.branch_id == branch_id else 0)

    ranked = sorted(((s, t) for t in rows if (s := score(t)) is not None), key=lambda x: -x[0])
    return TemplateConfig.model_validate(ranked[0][1].config) if ranked else TemplateConfig()
