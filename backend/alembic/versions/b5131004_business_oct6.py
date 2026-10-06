"""Separate procurement composition, wave forecasts and shared budgets."""

import sqlalchemy as sa

from alembic import op

revision = "b5131004"
down_revision = "b5131003"
branch_labels = None
depends_on = None


def execution_history_trigger(include_procurement):
    if op.get_bind().dialect.name != "postgresql":
        return
    fields = ["cancelled_quantity", "revision", "financing_deficit", "version"]
    if include_procurement:
        fields.append("procurement_at")
    arguments = ", ".join(f"'{field}'" for field in fields)
    op.execute("DROP TRIGGER crm_history_row ON executions")
    op.execute("CREATE TRIGGER crm_history_row BEFORE UPDATE OR DELETE ON executions "
               f"FOR EACH ROW EXECUTE FUNCTION crm_protect_history({arguments})")


def upgrade():
    op.add_column("executions", sa.Column("procurement_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_executions_procurement_at", "executions", ["procurement_at"])
    op.add_column("waves", sa.Column("budget_profile_id", sa.String(36), nullable=True))
    with op.batch_alter_table("waves") as batch:
        batch.create_foreign_key("fk_wave_budget_profile", "calculation_profiles", ["budget_profile_id"], ["id"])
    op.add_column("waves", sa.Column("budget_expenses", sa.JSON(), nullable=True))
    op.add_column("waves", sa.Column("budget_rates", sa.JSON(), nullable=True))
    op.create_table(
        "wave_forecasts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("wave_id", sa.String(36), sa.ForeignKey("waves.id"), nullable=False),
        sa.Column("product_group_id", sa.String(36), sa.ForeignKey("product_groups.id"), nullable=False),
        sa.Column("target_quantity", sa.Numeric(24, 0), nullable=False),
        sa.Column("unit_price_rub", sa.Numeric(24, 8), nullable=False),
        sa.Column("weight_per_unit", sa.Numeric(24, 8), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.UniqueConstraint("wave_id", "product_group_id"),
        sa.CheckConstraint("target_quantity > 0 AND unit_price_rub > 0 AND weight_per_unit >= 0"),
    )
    op.create_index("ix_wave_forecasts_wave_id", "wave_forecasts", ["wave_id"])
    execution_history_trigger(True)
    # Preserve completed handoffs and already operated orders; never promote a draft calculation.
    conn = op.get_bind()
    meta = sa.MetaData()
    executions = sa.Table("executions", meta, autoload_with=conn)
    approvals = sa.Table("approvals", meta, autoload_with=conn)
    allocations = sa.Table("wave_allocations", meta, autoload_with=conn)
    ordered = sa.Table("supplier_order_lines", meta, autoload_with=conn)
    for approval in conn.execute(sa.select(approvals).where(approvals.c.status == "approved")).mappings():
        for line in approval["snapshot"].get("lines", []):
            conn.execute(executions.update().where(
                executions.c.id == line["execution_id"], executions.c.revision == line["revision"],
                executions.c.procurement_at.is_(None),
                executions.c.quantity > executions.c.cancelled_quantity,
            ).values(procurement_at=approval["decided_at"] or approval["created_at"]))
    ids = sa.union(sa.select(allocations.c.execution_id), sa.select(ordered.c.execution_id))
    conn.execute(executions.update().where(executions.c.id.in_(ids), executions.c.procurement_at.is_(None))
                 .values(procurement_at=executions.c.created_at))
    # Copy the former common budget once, then stop deriving it from saved quotations.
    waves = sa.Table("waves", meta, autoload_with=conn)
    calculations = sa.Table("calculations", meta, autoload_with=conn)
    seen = set()
    for calculation in conn.execute(sa.select(calculations).order_by(calculations.c.created_at.desc(), calculations.c.id.desc())).mappings():
        snapshot = calculation["snapshot"]
        wave_id = (snapshot.get("wave") or {}).get("id")
        if not wave_id or wave_id in seen or snapshot.get("algorithm_version") != "itemized-v2":
            continue
        seen.add(wave_id)
        expenses = snapshot.get("resolved_expenses", (snapshot.get("input") or {}).get("expenses") or [])
        expenses = [row for row in expenses if row.get("scope", "WAVE") == "WAVE"
                    and row.get("stage") != "CLIENT_DELIVERY"]
        conn.execute(waves.update().where(waves.c.id == wave_id).values(
            budget_profile_id=calculation["profile_id"], budget_expenses=expenses,
            budget_rates=snapshot.get("rates") or [],
        ))


def downgrade():
    execution_history_trigger(False)
    op.drop_table("wave_forecasts")
    with op.batch_alter_table("waves") as batch:
        batch.drop_constraint("fk_wave_budget_profile", type_="foreignkey")
        batch.drop_column("budget_profile_id")
        batch.drop_column("budget_expenses")
        batch.drop_column("budget_rates")
    op.drop_index("ix_executions_procurement_at", table_name="executions")
    op.drop_column("executions", "procurement_at")
