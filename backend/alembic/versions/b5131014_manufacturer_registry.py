"""Registry of manufacturers: numbers for Excel forms, every known spelling, umbrella brands.

Each distinct spelling of a manufacturer in the nomenclature becomes a registry entry with a number; spellings
that differ only in letter case or spaces share one entry. Nomenclature is linked to its entry and keeps its
text. Merging spellings of one manufacturer is left to the registry screen.
"""

from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131014"
down_revision = "b5131013"
branch_labels = None
depends_on = None

manufacturers = sa.table("manufacturers", sa.column("id", sa.String), sa.column("name", sa.String),
                         sa.column("normalized_name", sa.String), sa.column("internal_code", sa.Integer),
                         sa.column("created_at", sa.DateTime), sa.column("version", sa.Integer))
aliases = sa.table("manufacturer_aliases", sa.column("id", sa.String), sa.column("created_at", sa.DateTime),
                   sa.column("version", sa.Integer), sa.column("manufacturer_id", sa.String),
                   sa.column("name", sa.String), sa.column("normalized_name", sa.String))
nomenclatures = sa.table("nomenclatures", sa.column("id", sa.String), sa.column("manufacturer", sa.String),
                         sa.column("manufacturer_id", sa.String))


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def backfill(conn) -> None:
    now = datetime.now(UTC)
    names, known = {}, {}
    for maker_id, name, key in conn.execute(sa.select(manufacturers.c.id, manufacturers.c.name,
                                                      manufacturers.c.normalized_name)):
        names[maker_id] = name
        known.setdefault(key, maker_id)
    # The most common spelling names the entry; on a tie, one with capitals reads better than all lower case.
    counts: Counter[str] = Counter(" ".join(text.split()) for (text,) in conn.execute(
        sa.select(nomenclatures.c.manufacturer)) if text and text.strip())
    spellings: dict[str, str] = {}
    for spelling in sorted(counts, key=lambda value: (-counts[value], value == value.lower(), value)):
        spellings.setdefault(normalized(spelling), spelling)
    for key, name in sorted(spellings.items(), key=lambda item: item[1].casefold()):
        if key not in known:
            maker_id = str(uuid4())
            conn.execute(manufacturers.insert().values(id=maker_id, name=name, normalized_name=key,
                                                       created_at=now, version=1))
            names[maker_id] = name
            known[key] = maker_id
    # Numbers follow the alphabet once; later entries take the next free number.
    for code, maker_id in enumerate(sorted(names, key=lambda key: (names[key].casefold(), key)), 1):
        conn.execute(manufacturers.update().where(manufacturers.c.id == maker_id).values(internal_code=code))
    for key, maker_id in known.items():
        conn.execute(aliases.insert().values(id=str(uuid4()), created_at=now, version=1, manufacturer_id=maker_id,
                                             name=names[maker_id], normalized_name=key))
    for nomenclature_id, text in conn.execute(sa.select(nomenclatures.c.id, nomenclatures.c.manufacturer)):
        if text and text.strip():
            conn.execute(nomenclatures.update().where(nomenclatures.c.id == nomenclature_id)
                         .values(manufacturer_id=known[normalized(text)]))


def upgrade():
    op.create_table(
        "manufacturer_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manufacturer_id", sa.String(36), sa.ForeignKey("manufacturers.id"), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("normalized_name", sa.String(250), nullable=False, unique=True),
    )
    op.create_index("ix_manufacturer_aliases_manufacturer_id", "manufacturer_aliases", ["manufacturer_id"])
    op.add_column("nomenclatures", sa.Column("manufacturer_id", sa.String(36), nullable=True))
    op.create_index("ix_nomenclatures_manufacturer_id", "nomenclatures", ["manufacturer_id"])
    with op.batch_alter_table("nomenclatures") as batch:
        batch.create_foreign_key("fk_nomenclatures_manufacturer", "manufacturers", ["manufacturer_id"], ["id"])
    for column in ("umbrella_id", "merged_into_id"):
        op.add_column("manufacturers", sa.Column(column, sa.String(36), nullable=True))
    op.add_column("manufacturers", sa.Column("internal_code", sa.Integer(), nullable=True))
    backfill(op.get_bind())
    # Spellings are unique among the aliases now; a merged entry keeps its old spelling as history.
    with op.batch_alter_table("manufacturers") as batch:
        batch.drop_constraint("manufacturers_normalized_name_key", type_="unique")
        batch.alter_column("internal_code", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_manufacturers_internal_code", ["internal_code"])
        batch.create_check_constraint("ck_manufacturers_internal_code", "internal_code > 0")
        batch.create_foreign_key("fk_manufacturers_umbrella", "manufacturers", ["umbrella_id"], ["id"])
        batch.create_foreign_key("fk_manufacturers_merged_into", "manufacturers", ["merged_into_id"], ["id"])
    op.create_index("ix_manufacturers_umbrella_id", "manufacturers", ["umbrella_id"])


def downgrade():
    # A merged entry may share its old spelling with the entry it was merged into.
    op.execute("UPDATE manufacturers SET normalized_name = normalized_name || ' #' || id "
               "WHERE merged_into_id IS NOT NULL")
    op.drop_index("ix_manufacturers_umbrella_id", "manufacturers")
    with op.batch_alter_table("manufacturers") as batch:
        batch.drop_constraint("fk_manufacturers_merged_into", type_="foreignkey")
        batch.drop_constraint("fk_manufacturers_umbrella", type_="foreignkey")
        batch.drop_constraint("ck_manufacturers_internal_code", type_="check")
        batch.drop_constraint("uq_manufacturers_internal_code", type_="unique")
        batch.drop_column("internal_code")
        batch.drop_column("merged_into_id")
        batch.drop_column("umbrella_id")
    # Entries added by the registry are kept.
    with op.batch_alter_table("manufacturers") as batch:
        batch.create_unique_constraint("manufacturers_normalized_name_key", ["normalized_name"])
    with op.batch_alter_table("nomenclatures") as batch:
        batch.drop_constraint("fk_nomenclatures_manufacturer", type_="foreignkey")
    op.drop_index("ix_nomenclatures_manufacturer_id", "nomenclatures")
    with op.batch_alter_table("nomenclatures") as batch:
        batch.drop_column("manufacturer_id")
    op.drop_index("ix_manufacturer_aliases_manufacturer_id", "manufacturer_aliases")
    op.drop_table("manufacturer_aliases")
