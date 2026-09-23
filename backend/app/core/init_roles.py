import os

from sqlalchemy import create_engine, text

from app.core.config import settings


def init_roles(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith("postgresql"):
        print(f"Skipping role init for non-Postgres URL: {url.split('://')[0]}")
        return

    api_password = os.environ.get("CRM_API_PASSWORD") or settings.crm_api_password
    backup_password = os.environ.get("CRM_BACKUP_PASSWORD") or settings.crm_backup_password

    # Connect to the target database
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # Check and create/update crm_api and crm_backup roles
        conn.execute(
            text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_api') THEN
                    CREATE ROLE crm_api WITH LOGIN PASSWORD '{api_password}';
                ELSE
                    ALTER ROLE crm_api WITH PASSWORD '{api_password}';
                END IF;

                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_backup') THEN
                    CREATE ROLE crm_backup WITH LOGIN PASSWORD '{backup_password}';
                ELSE
                    ALTER ROLE crm_backup WITH PASSWORD '{backup_password}';
                END IF;
            END
            $$;
            """)
        )

        # Grant DML permissions to crm_api
        conn.execute(
            text("""
            GRANT USAGE ON SCHEMA public TO crm_api;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_api;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_api;

            GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO crm_api;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO crm_api;

            GRANT USAGE ON SCHEMA public TO crm_backup;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO crm_backup;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crm_backup;

            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_backup;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO crm_backup;
            """)
        )
    print("Database roles crm_api and crm_backup verified and updated.")


if __name__ == "__main__":
    init_roles()
