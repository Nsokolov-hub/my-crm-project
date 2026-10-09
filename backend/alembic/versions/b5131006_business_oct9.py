"""Repair early commercial stages for requests with already issued proposals."""

import sqlalchemy as sa

from alembic import op

revision = "b5131006"
down_revision = "b5131005"
branch_labels = None
depends_on = None


def upgrade():
    # Saved documents and accepted prices are immutable. Only repair the
    # request milestone that older releases failed to advance.
    op.execute(sa.text("""
        UPDATE requests SET commercial_stage = CASE
            WHEN sale_confirmed_at IS NOT NULL THEN 'sale_confirmed'
            WHEN EXISTS (SELECT 1 FROM commercial_documents d
                         WHERE d.request_id = requests.id AND d.kind = 'invoice'
                         AND d.status != 'cancelled') THEN 'awaiting_payment'
            WHEN EXISTS (SELECT 1 FROM executions e
                         WHERE e.request_id = requests.id AND e.quantity > e.cancelled_quantity)
                         THEN 'composition_agreed'
            ELSE 'proposal_sent' END,
            version = version + 1
        WHERE commercial_stage IN ('new', 'clarification', 'collecting_quotes', 'quotes',
                                   'quote_given', 'calculation')
          AND EXISTS (SELECT 1 FROM commercial_documents d
                      WHERE d.request_id = requests.id AND d.kind = 'proposal'
                      AND d.status IN ('issued', 'sent', 'accepted'))
    """))


def downgrade():
    # A data repair cannot reconstruct which stale stages were intentional.
    # Keep the corrected request history when downgrading application code.
    pass
