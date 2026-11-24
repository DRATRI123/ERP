"""Customers and suppliers: the same endpoints, mounted twice with their own permissions.

No ``from __future__ import annotations`` here: FastAPI must resolve the per-role permission
aliases defined inside ``build_router`` at runtime.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas_masters import (
    AddressIn,
    AddressOut,
    AddressUpdateIn,
    GroupIn,
    GroupOut,
    PartyIn,
    PartyOut,
    PartyUpdateIn,
)
from app.modules.access.service import BusinessAccess
from app.modules.parties import service
from app.modules.parties.models import PartyRole


def build_router(role: PartyRole) -> APIRouter:
    plural = "customers" if role == PartyRole.CUSTOMER else "suppliers"
    router = APIRouter(prefix=f"/businesses/{{business_id}}/{plural}", tags=[plural])
    View = Annotated[BusinessAccess, Depends(require(f"{plural}.view"))]  # noqa: N806
    Manage = Annotated[BusinessAccess, Depends(require(f"{plural}.manage"))]  # noqa: N806

    @router.get("/groups", response_model=list[GroupOut], name=f"list_{role.value}_groups")
    def list_groups(access: View, db: DB) -> list[GroupOut]:
        return [GroupOut.model_validate(g) for g in service.list_groups(db, access, role)]

    @router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED,
                 name=f"create_{role.value}_group")
    def create_group(body: GroupIn, access: Manage, db: DB) -> GroupOut:
        g = service.create_group(db, access, role, body.name)
        db.commit()
        return GroupOut.model_validate(g)

    @router.patch("/groups/{group_id}", response_model=GroupOut, name=f"rename_{role.value}_group")
    def rename_group(group_id: uuid.UUID, body: GroupIn, access: Manage, db: DB) -> GroupOut:
        g = service.rename_group(db, access, service.get_group(db, access, group_id, role), body.name)
        db.commit()
        return GroupOut.model_validate(g)

    @router.get("", response_model=Page[PartyOut], name=f"list_{plural}")
    def list_parties(
        access: View, db: DB, params: Annotated[PageParams, Depends(page_params)],
        q: str | None = Query(None, max_length=100,
                              description="Name, phone, GSTIN, code or e-mail"),
        group_id: uuid.UUID | None = None, include_archived: bool = False,
    ) -> Page[PartyOut]:
        stmt = service.search(access, role, q=q, group_id=group_id,
                              include_archived=include_archived)
        rows, total = paginate(db, stmt, params)
        return Page(items=[PartyOut.model_validate(p) for p in rows], total=total,
                    limit=params.limit, offset=params.offset)

    @router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED,
                 name=f"create_{role.value}")
    def create_party(body: PartyIn, access: Manage, db: DB) -> PartyOut:
        data = body.model_dump(exclude={"addresses"})
        p = service.create_party(db, access, role, data, [a.model_dump() for a in body.addresses])
        db.commit()
        return PartyOut.model_validate(p)

    @router.get("/{party_id}", response_model=PartyOut, name=f"get_{role.value}")
    def get_party(party_id: uuid.UUID, access: View, db: DB) -> PartyOut:
        return PartyOut.model_validate(service.get_party(db, access, party_id, role))

    @router.patch("/{party_id}", response_model=PartyOut, name=f"update_{role.value}")
    def update_party(party_id: uuid.UUID, body: PartyUpdateIn, access: Manage, db: DB) -> PartyOut:
        p = service.update_party(db, access, role, service.get_party(db, access, party_id, role),
                                 body.model_dump(exclude_unset=True))
        db.commit()
        return PartyOut.model_validate(p)

    @router.post("/{party_id}/archive", response_model=PartyOut, name=f"archive_{role.value}")
    def archive(party_id: uuid.UUID, access: Manage, db: DB) -> PartyOut:
        p = service.set_archived(db, access, role, service.get_party(db, access, party_id, role), True)
        db.commit()
        return PartyOut.model_validate(p)

    @router.post("/{party_id}/restore", response_model=PartyOut, name=f"restore_{role.value}")
    def restore(party_id: uuid.UUID, access: Manage, db: DB) -> PartyOut:
        p = service.set_archived(db, access, role, service.get_party(db, access, party_id, role), False)
        db.commit()
        return PartyOut.model_validate(p)

    @router.post("/{party_id}/addresses", response_model=AddressOut,
                 status_code=status.HTTP_201_CREATED, name=f"add_{role.value}_address")
    def add_address(party_id: uuid.UUID, body: AddressIn, access: Manage, db: DB) -> AddressOut:
        a = service.add_address(db, access, role, service.get_party(db, access, party_id, role),
                                body.model_dump())
        db.commit()
        return AddressOut.model_validate(a)

    @router.patch("/{party_id}/addresses/{address_id}", response_model=AddressOut,
                  name=f"update_{role.value}_address")
    def update_address(party_id: uuid.UUID, address_id: uuid.UUID, body: AddressUpdateIn,
                       access: Manage, db: DB) -> AddressOut:
        a = service.update_address(db, access, role, service.get_party(db, access, party_id, role),
                                   address_id, body.model_dump(exclude_unset=True))
        db.commit()
        return AddressOut.model_validate(a)

    @router.delete("/{party_id}/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT,
                   name=f"delete_{role.value}_address")
    def delete_address(party_id: uuid.UUID, address_id: uuid.UUID, access: Manage, db: DB) -> None:
        service.delete_address(db, access, role, service.get_party(db, access, party_id, role),
                               address_id)
        db.commit()

    return router


customers = build_router(PartyRole.CUSTOMER)
suppliers = build_router(PartyRole.SUPPLIER)
