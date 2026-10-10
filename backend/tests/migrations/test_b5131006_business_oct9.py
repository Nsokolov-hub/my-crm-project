"""Repair old proposal milestones without touching closed requests or documents."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_oct9_repairs_only_early_stages_and_is_idempotent():
    path = Path(__file__).parents[1] / "alembic/versions/b5131006_business_oct9.py"
    spec = importlib.util.spec_from_file_location("business_oct9_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    requests = sa.Table("requests", meta, sa.Column("id", sa.String, primary_key=True),
        sa.Column("commercial_stage", sa.String), sa.Column("version", sa.Integer),
        sa.Column("sale_confirmed_at", sa.DateTime))
    documents = sa.Table("commercial_documents", meta, sa.Column("request_id", sa.String),
        sa.Column("kind", sa.String), sa.Column("status", sa.String), sa.Column("snapshot", sa.JSON))
    executions = sa.Table("executions", meta, sa.Column("request_id", sa.String),
        sa.Column("quantity", sa.Integer), sa.Column("cancelled_quantity", sa.Integer))
    meta.create_all(engine)
    with engine.begin() as conn:
        for identifier, stage, doc_status in (
            ("issued", "quote_given", "issued"), ("sent", "calculation", "sent"),
            ("accepted", "quote_given", "accepted"), ("invoice", "new", "accepted"),
            ("lost", "closed_lost", "issued"), ("confirmed", "sale_confirmed", "accepted"),
            ("advanced", "awaiting_payment", "issued"), ("cancelled", "new", "cancelled"),
        ):
            conn.execute(requests.insert().values(id=identifier, commercial_stage=stage, version=5))
            conn.execute(documents.insert().values(request_id=identifier, kind="proposal", status=doc_status,
                                                   snapshot={"number": "old", "tax": "153642.60"}))
        conn.execute(requests.insert().values(id="no-document", commercial_stage="quote_given", version=5))
        conn.execute(executions.insert().values(request_id="accepted", quantity=3, cancelled_quantity=0))
        conn.execute(documents.insert().values(request_id="invoice", kind="invoice", status="issued", snapshot={}))
        original_documents = conn.execute(sa.select(documents)).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
            migration.downgrade()
        result = {row.id: (row.commercial_stage, row.version) for row in conn.execute(sa.select(requests))}
        assert result == {
            "issued": ("proposal_sent", 6), "sent": ("proposal_sent", 6),
            "accepted": ("composition_agreed", 6), "invoice": ("awaiting_payment", 6),
            "lost": ("closed_lost", 5), "confirmed": ("sale_confirmed", 5),
            "advanced": ("awaiting_payment", 5), "cancelled": ("new", 5),
            "no-document": ("quote_given", 5),
        }
        assert conn.execute(sa.select(documents)).all() == original_documents
    engine.dispose()
