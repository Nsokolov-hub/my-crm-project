"""Create or update the runtime (DML only) and backup (read only) roles; run as the database owner."""

import os
from collections.abc import Mapping

from psycopg import sql
from sqlalchemy import create_engine

from app.core.config import settings

API_GRANTS = (
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
    "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {role}",
)
BACKUP_GRANTS = (
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
)
# Role -> (environment variable with its password, privileges).
ROLES = {"crm_api": ("CRM_API_PASSWORD", API_GRANTS), "crm_backup": ("CRM_BACKUP_PASSWORD", BACKUP_GRANTS)}
# Values that shipped as compose defaults or examples must never reach a database.
KNOWN_PASSWORDS = {"crm_api_password", "crm_backup_password"}
MIN_PASSWORD_LENGTH = 16


def role_password(variable: str) -> str:
    value = os.environ.get(variable) or getattr(settings, variable.lower())
    if value in KNOWN_PASSWORDS or len(value) < MIN_PASSWORD_LENGTH or value.startswith("replace-with"):
        raise SystemExit(
            f"Set {variable} in .env to an independent random password of at least "
            f"{MIN_PASSWORD_LENGTH} characters (scripts/init-env.py generates one)."
        )
    return value


def init_roles(database_url: str | None = None, names: Mapping[str, str] | None = None) -> None:
    """`names` maps a role to the name created in the cluster; tests use throwaway names."""
    url = database_url or settings.database_url
    if not url.startswith("postgresql"):
        print(f"Skipping role init for non-Postgres URL: {url.split('://')[0]}")
        return
    names = names or {role: role for role in ROLES}
    passwords = {role: role_password(variable) for role, (variable, _) in ROLES.items()}

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection, connection.connection.dbapi_connection.cursor() as cursor:
            for role, (_, grants) in ROLES.items():
                name = sql.Identifier(names[role])
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (names[role],))
                statement = ("ALTER ROLE {} WITH LOGIN PASSWORD {}" if cursor.fetchone()
                             else "CREATE ROLE {} WITH LOGIN PASSWORD {}")
                # DDL cannot take bind parameters; psycopg quotes the literal safely instead.
                cursor.execute(sql.SQL(statement).format(name, sql.Literal(passwords[role])))
                for grant in grants:
                    cursor.execute(sql.SQL(grant).format(role=name))
    finally:
        engine.dispose()
    print("Database roles crm_api and crm_backup verified and updated.")


if __name__ == "__main__":
    init_roles()
