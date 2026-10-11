import app.models  # noqa: F401  (registers every table on Base.metadata)
from alembic import context
from app.core.config import settings
from app.core.db import Base, engine

config = context.config
target_metadata = Base.metadata
# Created only by migrations: a PostgreSQL trigram index that the model metadata cannot express portably.
MIGRATION_ONLY_INDEXES = {"ix_counterparties_search_text_trgm"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "index" and name in MIGRATION_ONLY_INDEXES)

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    context.configure(
        connection=config.attributes["connection"], target_metadata=target_metadata, compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()
