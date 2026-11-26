"""Product photos: up to six per product, the first is the main one.

Uploads are checked to be real images and re-encoded (EXIF and GPS data dropped, longest side at
most 1200 px, JPEG / PNG kept transparent) so they stay small for phones. They are stored in the
document library (folder "Product photos", linked to the product).

Images are shown with signed URLs — ``/api/v1/public/img/{file_id}?s=…`` — so <img> tags work in
the app, on printed pages, in the public catalogue and in the salesperson portal without a login.
The signature is an HMAC of the file id with the server secret: unguessable, and it stops working
when the photo is removed (the file is moved to the trash).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog.models import Product

MAX_IMAGES = 6
MAX_SIDE = 1200
THUMB_SIDE = 320


def _sig(file_id: uuid.UUID | str, size: str = "") -> str:
    msg = f"product-image:{file_id}:{size}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def url(file_id: uuid.UUID | str, *, thumb: bool = False) -> str:
    size = "t" if thumb else ""
    return f"/api/v1/public/img/{file_id}?{'w=t&' if thumb else ''}s={_sig(file_id, size)}"


def check_sig(file_id: uuid.UUID, sig: str, size: str) -> bool:
    return hmac.compare_digest(sig, _sig(file_id, size))


def ids(p: Product) -> list[str]:
    return [str(i) for i in ((p.attributes or {}).get("images") or [])]


def out(p: Product) -> list[dict[str, str]]:
    return [{"id": i, "url": url(i), "thumb": url(i, thumb=True)} for i in ids(p)]


def _reencode(data: bytes, max_side: int) -> tuple[bytes, str]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationFailed("That is not a photo (use JPG, PNG or WebP)") from exc
    if opened.width * opened.height > 60_000_000:
        raise ValidationFailed("Photo is too large")
    img: Image.Image = ImageOps.exif_transpose(opened) or opened  # phone photos: keep them upright, then drop EXIF
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    if (img.mode in ("RGBA", "LA", "P") and "transparency" in img.info) or img.mode in ("RGBA", "LA"):
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        return buf.getvalue(), "png"
    img.convert("RGB").save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return buf.getvalue(), "jpg"


def add(db: Session, access: BusinessAccess, p: Product, *, data: bytes, filename: str) -> Product:
    access.require("products.manage")
    from app.modules.files import service as files

    have = ids(p)
    if len(have) >= MAX_IMAGES:
        raise ValidationFailed(f"Up to {MAX_IMAGES} photos per product — remove one first")
    clean, ext = _reencode(data, MAX_SIDE)
    stem = (filename.rsplit(".", 1)[0] or p.sku)[:80]
    f = files.upload(db, access, data=clean, filename=f"{stem}.{ext}", folder="Product photos",
                     description=f"Photo of {p.name}", tags=["product-photo"], entity_type="product", entity_id=p.id)
    p.attributes = {**(p.attributes or {}), "images": [*have, str(f.id)]}
    audit.record(db, action="product.image_added", entity_type="product", entity_id=p.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"file_id": str(f.id)})
    return p


def remove(db: Session, access: BusinessAccess, p: Product, file_id: uuid.UUID) -> Product:
    access.require("products.manage")
    from app.modules.files import service as files
    from app.modules.files.models import StoredFile

    have = ids(p)
    if str(file_id) not in have:
        raise NotFound("Photo not found")
    p.attributes = {**(p.attributes or {}), "images": [i for i in have if i != str(file_id)]}
    f = db.get(StoredFile, file_id)
    if f is not None and f.business_id == access.business.id and f.deleted_at is None:
        files.trash(db, access, f)  # the photo stays restorable from the library's trash
    audit.record(db, action="product.image_removed", entity_type="product", entity_id=p.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"file_id": str(file_id)})
    return p


def make_main(db: Session, access: BusinessAccess, p: Product, file_id: uuid.UUID) -> Product:
    access.require("products.manage")
    have = ids(p)
    if str(file_id) not in have:
        raise NotFound("Photo not found")
    p.attributes = {**(p.attributes or {}), "images": [str(file_id), *[i for i in have if i != str(file_id)]]}
    return p


def serve(db: Session, file_id: uuid.UUID, *, thumb: bool) -> tuple[bytes, str]:
    from app.modules.files import service as files
    from app.modules.files.models import StoredFile

    f = db.get(StoredFile, file_id)
    if f is None or f.deleted_at is not None or not {"product-photo", "chat-photo"} & set(f.tags or []):
        raise NotFound("Not found")
    data, _ = files.content(db, f)
    if thumb:
        data, ext = _reencode(data, THUMB_SIDE)
        return data, "image/png" if ext == "png" else "image/jpeg"
    return data, f.content_type
