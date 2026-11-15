"""Keep an unknown source distinct from a known direct visit."""

import sqlalchemy as sa

from alembic import op

revision = 'b5131031'
down_revision = 'b5131030'
branch_labels = None
depends_on = None


def replace_check(choices):
    table = 'site_request_attributions'
    checks = sa.inspect(op.get_bind()).get_check_constraints(table)
    current = next(row for row in checks if 'channel' in row['sqltext'])
    with op.batch_alter_table(table, naming_convention={'ck': 'ck_%(table_name)s_%(column_0_name)s'}) as batch:
        batch.drop_constraint(current['name'] or f'ck_{table}_', type_='check')
        batch.create_check_constraint('ck_site_attribution_channel', f'channel IS NULL OR channel IN ({choices})')


def upgrade():
    replace_check("'cpc', 'organic', 'referral', 'direct', 'email', 'social', 'unknown'")


def downgrade():
    op.execute(sa.text("UPDATE site_request_attributions SET channel = NULL WHERE channel = 'unknown'"))
    replace_check("'cpc', 'organic', 'referral', 'direct', 'email', 'social'")
