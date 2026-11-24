"""Document library endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.files import service as files
from app.modules.files.models import StoredFile

router = APIRouter(prefix="/businesses/{business_id}", tags=["files"])
View = Annotated[BusinessAccess, Depends(require("files.view"))]
Manage = Annotated[BusinessAccess, Depends(require("files.view", "files.manage"))]


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    folder: str
    description: str | None
    tags: list[str]
    content_type: str
    size: int
    current_version: int
    entity_type: str | None
    entity_id: uuid.UUID | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    original_name: str
    content_type: str
    size: int
    sha256: str
    note: str | None
    uploaded_by_id: uuid.UUID | None
    created_at: datetime


class FileDetailOut(FileOut):
    versions: list[VersionOut]


class FileUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    folder: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=1000)
    tags: list[str] | None = Field(None, max_length=20)
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None


def _read(upload: UploadFile) -> bytes:
    data = upload.file.read(files.max_bytes() + 1)
    if len(data) > files.max_bytes():
        raise ValidationFailed("The file is too large", code="file_too_large")
    return data


@router.get("/files", response_model=Page[FileOut])
def list_files(access: View, db: DB, params: Annotated[PageParams, Depends(page_params)],
               q: str | None = Query(None, max_length=100), folder: str | None = Query(None, max_length=300),
               recursive: bool = False, entity_type: str | None = None, entity_id: uuid.UUID | None = None,
               tag: str | None = Query(None, max_length=30), trash: bool = False) -> Page[FileOut]:
    stmt = files.visible_files(db, access, trash=trash)
    if folder:
        f = files.clean_folder(folder)
        stmt = stmt.where(or_(StoredFile.folder == f, StoredFile.folder.startswith(f.rstrip("/") + "/")) if recursive
                          else StoredFile.folder == f)
    if entity_type:
        stmt = stmt.where(StoredFile.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(StoredFile.entity_id == entity_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(StoredFile.name).like(like), func.lower(func.coalesce(StoredFile.description, "")).like(like)))
    rows, total = paginate(db, stmt.order_by(StoredFile.updated_at.desc()), params)
    if tag:  # tags are a JSON list; filtered after the page is read for database portability
        rows = [r for r in rows if tag.lower() in r.tags]
    return Page(items=[FileOut.model_validate(r) for r in rows], total=total, limit=params.limit, offset=params.offset)


@router.get("/files/folders")
def list_folders(access: View, db: DB) -> dict[str, Any]:
    return {"folders": files.folders(db, access), "usage": files.usage(db, access.business.id),
            "max_upload_mb": files.max_bytes() // (1024 * 1024)}


@router.post("/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
def upload_file(access: Manage, db: DB, file: UploadFile = File(...),
                folder: str | None = Form(None), description: str | None = Form(None),
                tags: str | None = Form(None, description="Comma-separated"),
                entity_type: str | None = Form(None), entity_id: uuid.UUID | None = Form(None)) -> FileOut:
    f = files.upload(db, access, data=_read(file), filename=file.filename or "file", folder=folder,
                     description=description or None, tags=(tags or "").split(",") if tags else [],
                     entity_type=entity_type or None, entity_id=entity_id)
    db.commit()
    return FileOut.model_validate(f)


@router.get("/files/{file_id}", response_model=FileDetailOut)
def get_file(file_id: uuid.UUID, access: View, db: DB) -> FileDetailOut:
    f = files.get_file(db, access, file_id, include_deleted=True)
    return FileDetailOut.model_validate({**FileOut.model_validate(f).model_dump(),
                                         "versions": [VersionOut.model_validate(v) for v in files.versions(db, f)]})


@router.get("/files/{file_id}/download", response_class=Response)
def download(file_id: uuid.UUID, access: View, db: DB, version: int | None = Query(None, ge=1)) -> Response:
    f = files.get_file(db, access, file_id, include_deleted=True)
    data, v = files.content(db, f, version)
    name = f.name if v.version == f.current_version else f"v{v.version} - {v.original_name}"
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "'")
    return Response(data, media_type=v.content_type, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
        "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store"})


@router.post("/files/{file_id}/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED)
def add_version(file_id: uuid.UUID, access: Manage, db: DB, file: UploadFile = File(...),
                note: str | None = Form(None, max_length=300)) -> VersionOut:
    f = files.get_file(db, access, file_id)
    v = files.add_version(db, access, f, data=_read(file), filename=file.filename or f.name, note=note or None)
    db.commit()
    return VersionOut.model_validate(v)


@router.patch("/files/{file_id}", response_model=FileOut)
def update_file(file_id: uuid.UUID, body: FileUpdateIn, access: Manage, db: DB) -> FileOut:
    f = files.update(db, access, files.get_file(db, access, file_id), body.model_dump(exclude_unset=True))
    db.commit()
    return FileOut.model_validate(f)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def trash_file(file_id: uuid.UUID, access: Manage, db: DB, permanent: bool = False) -> None:
    """Moves the file to the trash; ``permanent=true`` on a trashed file deletes it for good."""
    f = files.get_file(db, access, file_id, include_deleted=True)
    if permanent:
        files.purge(db, access, f)
    else:
        files.trash(db, access, f)
    db.commit()


@router.post("/files/{file_id}/restore", response_model=FileOut)
def restore_file(file_id: uuid.UUID, access: Manage, db: DB) -> FileOut:
    f = files.get_file(db, access, file_id, include_deleted=True)
    files.restore(db, access, f)
    db.commit()
    return FileOut.model_validate(f)
