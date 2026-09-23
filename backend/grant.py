import psycopg

conn = psycopg.connect("postgresql://nikolajsokolov@localhost:5432/postgres")
conn.autocommit = True
conn.execute("GRANT CREATE ON DATABASE crm_test_db TO crm_test;")
conn.close()
