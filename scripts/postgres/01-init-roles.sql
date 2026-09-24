-- Create or update API/Worker Role (Runtime) and Backup Role idempotently
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_api') THEN
        CREATE ROLE crm_api WITH LOGIN PASSWORD 'crm_api_password';
    ELSE
        ALTER ROLE crm_api WITH PASSWORD 'crm_api_password';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_backup') THEN
        CREATE ROLE crm_backup WITH LOGIN PASSWORD 'crm_backup_password';
    ELSE
        ALTER ROLE crm_backup WITH PASSWORD 'crm_backup_password';
    END IF;
END
$$;

-- Grant API Role privileges (DML only, no DDL/DROP)
GRANT USAGE ON SCHEMA public TO crm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_api;

GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO crm_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO crm_api;

-- Grant Backup Role privileges (SELECT only)
GRANT USAGE ON SCHEMA public TO crm_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO crm_backup;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crm_backup;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_backup;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO crm_backup;
