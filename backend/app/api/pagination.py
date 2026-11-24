"""Offset pagination shared by list endpoints."""

from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


def paginate(db: Session, stmt: Select, params: PageParams) -> tuple[list, int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.limit(params.limit).offset(params.offset)).unique())
    return rows, total
