"""Document library: upload, version, link, find, download and trash files.

Security rules:
* The type is decided from the file's **content** (magic bytes), never from its name or the
  browser's Content-Type. HTML, SVG, scripts and executables are refused, so a stored file can
  never run in the ERP's origin; downloads are always ``attachment`` with ``nosniff``.
* A file linked to a record (invoice, customer, product, expense, payment) is visible only to
  members who can see that record — including branch scope for documents and payments.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.files.models import FileBlob, FileVersion, StoredFile

ENTITY_TYPES = ("document", "party", "product", "expense", "payment")
OFFICE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
}
LEGACY_OFFICE = {"doc": "application/msword", "xls": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint"}
TEXT = {"txt": "text/plain", "csv": "text/csv", "json": "application/json", "md": "text/markdown", "log": "text/plain"}
_DANGEROUS_TEXT = re.compile(rb"<\s*(!doctype|html|script|svg|iframe|object|embed)\b", re.IGNORECASE)


def max_bytes() -> int:
    return get_settings().max_upload_mb * 1024 * 1024


# ---------------------------------------------------------------------------- validation

def detect_type(data: bytes, filename: str) -> str:
    """The content type from the bytes themselves, or ValidationFailed for anything not allowed."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"PK\x03\x04"):
        return OFFICE.get(ext, "application/zip")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if ext in LEGACY_OFFICE:
            return LEGACY_OFFICE[ext]
        raise ValidationFailed("This file type is not allowed", code="file_type_not_allowed")
    # Plain text: must be valid UTF-8 without NULs, and must not be markup that a browser would run.
    if b"\x00" not in data[:65536]:
        try:
            data[:65536].decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if _DANGEROUS_TEXT.search(data[:65536]):
                raise ValidationFailed("HTML, SVG and script files are not allowed", code="file_type_not_allowed")
            return TEXT.get(ext, "text/plain")
    raise ValidationFailed("This file type is not allowed. Upload PDF, images, Office documents, CSV or text.",
                           code="file_type_not_allowed")


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .")
    if not name:
        raise ValidationFailed("The file needs a name", code="invalid_name")
    return name[:200]


def clean_folder(folder: str | None) -> str:
    parts = [p.strip() for p in (folder or "/").replace("\\", "/").split("/") if p.strip()]
    for p in parts:
        if p in (".", "..") or not all(c.isprintable() for c in p) or len(p) > 60:
            raise ValidationFailed("Invalid folder name", code="invalid_folder")
    if len(parts) > 6:
        raise ValidationFailed("Folders can be at most 6 levels deep", code="invalid_folder")
    return "/" + "/".join(parts)


def clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in tags or []:
        t = raw.strip().lower()[:30]
        if t and t not in out:
            out.append(t)
    return out[:20]


# ---------------------------------------------------------------------------- linked records

def _entity_condition(db: Session, access: BusinessAccess) -> ColumnElement[bool]:
    """SQL condition: which linked files this member may see."""
    from app.modules.billing.doctypes import SPECS
    from app.modules.billing.models import Document, Payment, PaymentDirection
    from app.modules.parties.models import Party

    bid = access.business.id
    conds: list[ColumnElement[bool]] = [StoredFile.entity_type.is_(None)]
    doc_types = [t for t, s in SPECS.items() if access.can(s.view_permission)]
    if doc_types:
        docs = select(Document.id).where(Document.business_id == bid, Document.doc_type.in_(doc_types))
        if not access.all_branches:
            docs = docs.where(Document.branch_id.in_(access.branch_ids or [uuid.uuid4()]))
        conds.append(and_(StoredFile.entity_type == "document", StoredFile.entity_id.in_(docs)))
    roles = []
    if access.can("customers.view"):
        roles.append(Party.is_customer.is_(True))
    if access.can("suppliers.view"):
        roles.append(Party.is_supplier.is_(True))
    if roles:
        conds.append(and_(StoredFile.entity_type == "party", StoredFile.entity_id.in_(
            select(Party.id).where(Party.business_id == bid, or_(*roles)))))
    if access.can("products.view"):
        conds.append(StoredFile.entity_type == "product")
    if access.can("expenses.view"):
        conds.append(StoredFile.entity_type == "expense")
    if access.can("payments.view"):
        dirs = [d for d, perm in ((PaymentDirection.IN, "invoices.view"), (PaymentDirection.OUT, "purchases.view")) if access.can(perm)]
        pays = select(Payment.id).where(Payment.business_id == bid, Payment.direction.in_(dirs) if dirs else false())
        if not access.all_branches:
            pays = pays.where(Payment.branch_id.in_(access.branch_ids or [uuid.uuid4()]))
        conds.append(and_(StoredFile.entity_type == "payment", StoredFile.entity_id.in_(pays)))
    return or_(*conds)


def _can_see_entity(db: Session, access: BusinessAccess, entity_type: str, entity_id: uuid.UUID) -> bool:
    """Same rule as the listing filter, for one record."""
    from app.modules.accounting.models import Expense
    from app.modules.billing.doctypes import SPECS
    from app.modules.billing.models import Document, Payment, PaymentDirection
    from app.modules.catalog.models import Product
    from app.modules.parties.models import Party

    model = {"document": Document, "party": Party, "product": Product, "expense": Expense, "payment": Payment}[entity_type]
    row = db.get(model, entity_id)
    if row is None or row.business_id != access.business.id:
        return False
    if entity_type == "document":
        return access.can(SPECS[row.doc_type].view_permission) and access.can_access_branch(row.branch_id)
    if entity_type == "party":
        return (row.is_customer and access.can("customers.view")) or (row.is_supplier and access.can("suppliers.view"))
    if entity_type == "product":
        return access.can("products.view")
    if entity_type == "expense":
        return access.can("expenses.view")
    need = "invoices.view" if row.direction == PaymentDirection.IN else "purchases.view"
    return access.can("payments.view") and access.can(need) and access.can_access_branch(row.branch_id)


def check_entity(db: Session, access: BusinessAccess, entity_type: str | None, entity_id: uuid.UUID | None) -> None:
    """The record a file is linked to must exist in this business and be visible to the member."""
    if entity_type is None and entity_id is None:
        return
    if entity_type not in ENTITY_TYPES or entity_id is None:
        raise ValidationFailed(f"entity_type must be one of {', '.join(ENTITY_TYPES)} with an entity_id")
    if not _can_see_entity(db, access, entity_type, entity_id):
        raise NotFound("The linked record was not found")


# ---------------------------------------------------------------------------- queries

def visible_files(db: Session, access: BusinessAccess, *, trash: bool = False):  # type: ignore[no-untyped-def]
    return select(StoredFile).where(
        StoredFile.business_id == access.business.id,
        StoredFile.deleted_at.is_not(None) if trash else StoredFile.deleted_at.is_(None),
        _entity_condition(db, access))


def get_file(db: Session, access: BusinessAccess, file_id: uuid.UUID, *, include_deleted: bool = False) -> StoredFile:
    f = db.get(StoredFile, file_id)
    if (f is None or f.business_id != access.business.id or (f.deleted_at and not include_deleted)
            or (f.entity_type and not _can_see_entity(db, access, f.entity_type, f.entity_id))):  # type: ignore[arg-type]
        raise NotFound("File not found")
    return f


def versions(db: Session, f: StoredFile) -> list[FileVersion]:
    return list(db.scalars(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.version.desc())))


def folders(db: Session, access: BusinessAccess) -> list[dict[str, Any]]:
    rows = db.execute(select(StoredFile.folder, func.count()).where(
        StoredFile.business_id == access.business.id, StoredFile.deleted_at.is_(None), _entity_condition(db, access))
        .group_by(StoredFile.folder).order_by(StoredFile.folder)).all()
    return [{"folder": f, "files": int(n)} for f, n in rows]


def usage(db: Session, business_id: uuid.UUID) -> dict[str, int]:
    size, n = db.execute(select(func.coalesce(func.sum(FileBlob.size), 0), func.count()).where(
        FileBlob.business_id == business_id)).one()
    return {"stored_bytes": int(size), "blobs": int(n)}


# ---------------------------------------------------------------------------- changes

def _blob(db: Session, business_id: uuid.UUID, data: bytes) -> FileBlob:
    digest = hashlib.sha256(data).hexdigest()
    blob = db.scalar(select(FileBlob).where(FileBlob.business_id == business_id, FileBlob.sha256 == digest))
    if blob is None:
        blob = FileBlob(business_id=business_id, sha256=digest, size=len(data), content=data)
        db.add(blob)
        db.flush()
    return blob


def _check_size(data: bytes) -> None:
    if not data:
        raise ValidationFailed("The file is empty", code="empty_file")
    if len(data) > max_bytes():
        raise ValidationFailed(f"Files can be at most {get_settings().max_upload_mb} MB", code="file_too_large")


def upload(db: Session, access: BusinessAccess, *, data: bytes, filename: str, folder: str | None = None,
           description: str | None = None, tags: list[str] | None = None,
           entity_type: str | None = None, entity_id: uuid.UUID | None = None) -> StoredFile:
    _check_size(data)
    name = clean_name(filename)
    ctype = detect_type(data, name)
    check_entity(db, access, entity_type, entity_id)
    blob = _blob(db, access.business.id, data)
    f = StoredFile(business_id=access.business.id, name=name, folder=clean_folder(folder), description=description,
                   tags=clean_tags(tags), content_type=ctype, size=len(data), current_version=1,
                   entity_type=entity_type, entity_id=entity_id, created_by_id=access.user.id)
    db.add(f)
    db.flush()
    db.add(FileVersion(file_id=f.id, version=1, blob_id=blob.id, original_name=name, content_type=ctype,
                       size=len(data), sha256=blob.sha256, uploaded_by_id=access.user.id))
    audit.record(db, action="file.uploaded", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": name, "folder": f.folder, "size": len(data),
                                                   "sha256": blob.sha256, "linked": entity_type})
    return f


def add_version(db: Session, access: BusinessAccess, f: StoredFile, *, data: bytes, filename: str,
                note: str | None = None) -> FileVersion:
    _check_size(data)
    name = clean_name(filename)
    ctype = detect_type(data, name)
    blob = _blob(db, access.business.id, data)
    f.current_version += 1
    f.size, f.content_type = len(data), ctype
    v = FileVersion(file_id=f.id, version=f.current_version, blob_id=blob.id, original_name=name, content_type=ctype,
                    size=len(data), sha256=blob.sha256, note=note, uploaded_by_id=access.user.id)
    db.add(v)
    audit.record(db, action="file.version_added", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"version": v.version, "sha256": blob.sha256})
    return v


def content(db: Session, f: StoredFile, version: int | None = None) -> tuple[bytes, FileVersion]:
    v = db.scalar(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.version == (version or f.current_version)))
    if v is None:
        raise NotFound("That version does not exist")
    blob = db.get(FileBlob, v.blob_id)
    assert blob is not None
    return blob.content, v


def update(db: Session, access: BusinessAccess, f: StoredFile, changes: dict[str, Any]) -> StoredFile:
    if "name" in changes and changes["name"] is not None:
        f.name = clean_name(changes["name"])
    if "folder" in changes:
        f.folder = clean_folder(changes["folder"])
    if "description" in changes:
        f.description = changes["description"]
    if "tags" in changes and changes["tags"] is not None:
        f.tags = clean_tags(changes["tags"])
    if "entity_type" in changes or "entity_id" in changes:
        et, eid = changes.get("entity_type", f.entity_type), changes.get("entity_id", f.entity_id)
        check_entity(db, access, et, eid)
        f.entity_type, f.entity_id = et, eid
    audit.record(db, action="file.updated", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={k: str(v) for k, v in changes.items()})
    return f


def trash(db: Session, access: BusinessAccess, f: StoredFile) -> None:
    f.deleted_at, f.deleted_by_id = datetime.now(UTC), access.user.id
    audit.record(db, action="file.trashed", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": f.name})


def restore(db: Session, access: BusinessAccess, f: StoredFile) -> None:
    f.deleted_at = f.deleted_by_id = None
    audit.record(db, action="file.restored", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": f.name})


def purge(db: Session, access: BusinessAccess, f: StoredFile) -> None:
    """Delete a trashed file for good, with its versions; contents nobody else uses go too."""
    if f.deleted_at is None:
        raise ValidationFailed("Move the file to the trash first", code="not_in_trash")
    blob_ids = {v.blob_id for v in versions(db, f)}
    audit.record(db, action="file.purged", entity_type="file", entity_id=f.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": f.name})
    db.delete(f)
    db.flush()
    for bid in blob_ids:
        if not db.scalar(select(FileVersion.id).where(FileVersion.blob_id == bid).limit(1)):
            blob = db.get(FileBlob, bid)
            if blob is not None:
                db.delete(blob)
