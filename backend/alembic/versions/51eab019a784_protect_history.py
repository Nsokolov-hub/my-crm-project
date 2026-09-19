"""Protect recorded versions and ledger history from UPDATE, DELETE and TRUNCATE."""

from alembic import op

revision = "51eab019a784"
down_revision = "2abdec759d06"
branch_labels = None
depends_on = None

# Mutable lifecycle fields are explicit; every other field remains a recorded fact.
PROTECTED = {
    "audit_events": (),
    "request_item_revisions": (),
    "quotes": (),
    "calculations": (),
    "calculation_profiles": (),
    "payment_allocations": (),
    "payment_reversals": (),
    "fulfillment_events": (),
    "supplier_requests": ("sent_at", "sent_channel", "version"),
    "commercial_documents": ("status", "sent_at", "sent_channel", "version"),
    "executions": ("cancelled_quantity", "revision", "financing_deficit", "version"),
    "approvals": ("status", "reason", "decided_by", "decided_at", "version"),
    "payments": ("status", "confirmed_by", "confirmed_at", "version"),
    "wave_allocations": ("active", "reason", "version"),
}


def upgrade():
    op.execute("""
        CREATE FUNCTION crm_protect_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'UPDATE' OR TG_NARGS = 0 THEN
                RAISE EXCEPTION 'CRM_RECORDED_FACT_IMMUTABLE' USING ERRCODE = '23514';
            END IF;
            IF (to_jsonb(OLD) - TG_ARGV) IS DISTINCT FROM (to_jsonb(NEW) - TG_ARGV) THEN
                RAISE EXCEPTION 'CRM_RECORDED_FACT_IMMUTABLE' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    for table, fields in PROTECTED.items():
        arguments = ", ".join(f"'{field}'" for field in fields)
        op.execute(
            f"CREATE TRIGGER crm_history_row BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION crm_protect_history({arguments})"
        )
        op.execute(
            f"CREATE TRIGGER crm_history_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION crm_protect_history()"
        )


def downgrade():
    for table in reversed(PROTECTED):
        op.execute(f"DROP TRIGGER crm_history_truncate ON {table}")
        op.execute(f"DROP TRIGGER crm_history_row ON {table}")
    op.execute("DROP FUNCTION crm_protect_history()")
