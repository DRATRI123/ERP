"""business modules: switch the pharmacy module on where it is already in use

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-25 10:00:00
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

businesses = sa.table('businesses', sa.column('id', sa.Uuid()), sa.column('settings', sa.JSON()))


def upgrade() -> None:
    # Modules live in businesses.settings["modules"]; pharmacy is off by default. Businesses that
    # already have doctors or medicines with a schedule / salt keep seeing the pharmacy screens.
    bind = op.get_bind()
    using = {uuid.UUID(str(r[0])) for r in bind.execute(sa.text(
        "SELECT business_id FROM doctors UNION "
        "SELECT business_id FROM products WHERE drug_schedule <> 'none' OR composition IS NOT NULL"))}
    for bid, stored in bind.execute(sa.select(businesses.c.id, businesses.c.settings)):
        if uuid.UUID(str(bid)) not in using:
            continue
        settings = dict(stored or {})
        modules = dict(settings.get('modules') or {})
        modules.setdefault('pharmacy', True)
        settings['modules'] = modules
        bind.execute(businesses.update().where(businesses.c.id == bid).values(settings=settings))


def downgrade() -> None:
    pass  # settings keys are harmless to keep
