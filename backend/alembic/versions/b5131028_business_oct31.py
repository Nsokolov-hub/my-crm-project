"""Business remarks of 31 October 2026: saved filters of an employee, several addresses of a supplier mail."""

import sqlalchemy as sa

from alembic import op

revision = "b5131028"
down_revision = "b5131027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])
    with op.batch_alter_table("supplier_mails") as batch:
        batch.alter_column("recipient", existing_type=sa.String(length=254), type_=sa.String(length=1000),
                           existing_nullable=False)


def downgrade():
    with op.batch_alter_table("supplier_mails") as batch:
        batch.alter_column("recipient", existing_type=sa.String(length=1000), type_=sa.String(length=254),
                           existing_nullable=False)
    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
