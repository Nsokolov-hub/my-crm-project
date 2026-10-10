"""Shared fixtures: every test package gets them without importing other test modules."""

import app.models  # noqa: F401  (every table is known to Base.metadata, whatever runs first)
from tests.support.commerce import commerce  # noqa: F401
from tests.support.crm import crm  # noqa: F401
from tests.support.postgres import postgres_commerce  # noqa: F401
