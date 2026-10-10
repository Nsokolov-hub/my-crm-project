"""Upgrade legacy records without changing public numbers or exact saved dates."""

import importlib.util
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_oct2_upgrade_preserves_legacy_records_and_seeds_compact_counter():
    path = Path(__file__).parents[1] / "alembic/versions/b5131002_business_workflows.py"
    spec = importlib.util.spec_from_file_location("business_oct2_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    requests = sa.Table(
        "requests",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("number", sa.String(80)),
    )
    sa.Table("request_items", metadata, sa.Column("id", sa.String(36), primary_key=True))
    parties = sa.Table("counterparties", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("files", metadata, sa.Column("id", sa.String(36), primary_key=True))
    rfqs = sa.Table(
        "supplier_requests",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("counterparties.id"), nullable=False),
    )
    waves = sa.Table(
        "waves",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        *[
            sa.Column(f"{prefix}_date", sa.Date(), nullable=False)
            for prefix in ("close", "departure", "arrival")
        ],
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            requests.insert(),
            [{"id": "legacy", "number": "З-2026-00005"}, {"id": "compact", "number": "Z1000"}],
        )
        connection.execute(parties.insert().values(id="supplier"))
        connection.execute(rfqs.insert().values(id="old-rfq", supplier_id="supplier"))
        connection.execute(
            waves.insert().values(
                id="wave",
                close_date=date(2021, 1, 1),
                departure_date=date(2021, 1, 4),
                arrival_date=date(2021, 1, 11),
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert list(connection.scalars(sa.select(requests.c.number).order_by(requests.c.id))) == [
            "Z1000",
            "З-2026-00005",
        ]
        reflected = sa.MetaData()
        reflected.reflect(connection)
        counter = reflected.tables["number_counters"]
        assert connection.scalar(sa.select(counter.c.value).where(counter.c.key == "requests")) == 27000
        row = connection.execute(sa.select(reflected.tables["waves"])).mappings().one()
        assert (row["close_week"], row["close_year"]) == (53, 2020)
        assert (row["departure_week"], row["departure_year"]) == (1, 2021)
        assert row["close_date"] == date(2021, 1, 1)
        upgraded_rfqs = reflected.tables["supplier_requests"]
        assert connection.scalar(sa.select(upgraded_rfqs.c.supplier_id)) == "supplier"
        connection.execute(upgraded_rfqs.insert().values(id="common-rfq", supplier_id=None))
        items = reflected.tables["request_items"]
        connection.execute(items.insert().values(id="old-item"))
        assert connection.scalar(sa.select(items.c.source_format)) == {}
    engine.dispose()
