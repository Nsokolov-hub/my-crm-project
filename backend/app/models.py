"""Import every ORM module so Base.metadata knows all tables (Alembic, tests and scripts)."""

import app.business.models  # noqa: F401
import app.commerce.models  # noqa: F401
import app.communication.models  # noqa: F401
import app.core.models  # noqa: F401
import app.crm.models  # noqa: F401
