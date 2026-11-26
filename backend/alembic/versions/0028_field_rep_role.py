"""roles: add the "Field salesperson / MR" system role to existing businesses

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-26 10:00:00
"""
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = '0028'
down_revision: str | None = '0027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

roles = sa.table('roles', sa.column('id', sa.Uuid()), sa.column('business_id', sa.Uuid()), sa.column('code', sa.String()),
                 sa.column('name', sa.String()), sa.column('description', sa.Text()), sa.column('is_system', sa.Boolean()),
                 sa.column('created_at', sa.DateTime(timezone=True)), sa.column('updated_at', sa.DateTime(timezone=True)))
businesses = sa.table('businesses', sa.column('id', sa.Uuid()))
CODE = 'field_rep'


def upgrade() -> None:
    bind = op.get_bind()
    have = {uuid.UUID(str(r[0])) for r in bind.execute(sa.select(roles.c.business_id).where(roles.c.code == CODE))}
    now = datetime.now(UTC)
    for (bid,) in bind.execute(sa.select(businesses.c.id)):
        if uuid.UUID(str(bid)) in have:
            continue
        bind.execute(roles.insert().values(
            id=uuid.uuid4(), business_id=bid, code=CODE, name='Field salesperson / MR',
            description='Uses only the salesperson portal (/sp) on the phone: beats, orders, collections, visits, new customers',
            is_system=True, created_at=now, updated_at=now))


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(sa.text("SELECT COUNT(*) FROM memberships m JOIN roles r ON r.id = m.role_id WHERE r.code = 'field_rep'")).scalar()
    if not used:
        bind.execute(roles.delete().where(roles.c.code == CODE))
