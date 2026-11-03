"""Extra freight of a supplier's quote sheet.

Business remark of 3 November 2026: a large order sometimes ships by extra freight and its logistics cost more. The
manager marks the supplier's quote with «Экстрафрахт» and the amount recommended to lay into the calculation; the
calculation form shows it as a note while positions of that quote are selected. Kept as
{"amount": "…", "currency": "USD", "comment": "…", "set_by": "<user id>", "set_at": "<ISO moment>"}.
"""

import sqlalchemy as sa

from alembic import op

revision = "b5131030"
down_revision = "b5131029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quote_sheets", sa.Column("extra_freight", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("quote_sheets", "extra_freight")
