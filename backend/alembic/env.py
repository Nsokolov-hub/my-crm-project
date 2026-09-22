import app.commerce.models  # noqa: F401
import app.communication.models  # noqa: F401
import app.core.models  # noqa: F401
import app.crm.models  # noqa: F401
from alembic import context
from app.core.config import settings
from app.core.db import Base, engine
from app.core.models import *  # noqa
from app.crm.models import *  # noqa
from app.commerce.models import *  # noqa

config = context.config
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    context.configure(
        connection=config.attributes["connection"], target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
