"""Import every ORM module so Base.metadata knows all tables (Alembic, tests and scripts)."""

import app.business.models  # noqa: F401
import app.commerce.models  # noqa: F401
import app.communication.models  # noqa: F401
import app.core.models  # noqa: F401
import app.crm.models  # noqa: F401
import app.integrations.models  # noqa: F401

# Listeners that turn changes into events for the site (flow F2) in every process that writes.
import app.integrations.site_events  # noqa: F401
