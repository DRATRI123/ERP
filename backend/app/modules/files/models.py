"""Document library: files with versions, stored in the database (de-duplicated per business).

Keeping the bytes in the database means encrypted backups, restore drills and the
SQLite → PostgreSQL migration cover files with no extra machinery. Uploads are limited in size
(``ERP_MAX_UPLOAD_MB``) so this stays sensible for a business's documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime


class FileBlob(UUIDPrimaryKey, Base):
    __tablename__ = "file_blobs"
    __table_args__ = (UniqueConstraint("business_id", "sha256"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class StoredFile(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_business_folder", "business_id", "folder"),
        Index("ix_files_entity", "business_id", "entity_type", "entity_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    folder: Mapped[str] = mapped_column(String(300), nullable=False, default="/")
    description: Mapped[str | None] = mapped_column(String(1000))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Optional link to a record (invoice, customer, product…). Viewing a linked file also
    # requires permission to see that record.
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class FileVersion(UUIDPrimaryKey, Base):
    __tablename__ = "file_versions"
    __table_args__ = (UniqueConstraint("file_id", "version"),)

    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("file_blobs.id", ondelete="RESTRICT"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
