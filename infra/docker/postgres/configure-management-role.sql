\set ON_ERROR_STOP on
\getenv management_user SOURCE_MANAGEMENT_USER
\getenv management_password SOURCE_MANAGEMENT_PASSWORD
\getenv migration_user DB_MIGRATION_USER
\getenv app_user DB_APP_USER
\getenv writer_user SOURCE_WRITER_USER

-- Optional, separately invoked bootstrap. No data privileges are granted here.
SELECT count(DISTINCT name)=5 AND bool_and(length(name)>0) AS roles_valid
FROM (VALUES (current_user), (:'migration_user'), (:'app_user'), (:'writer_user'), (:'management_user')) AS roles(name)
\gset
\if :roles_valid
\else
  \echo 'Management, Admin, Migration, Runtime and Source Writer must be distinct'
  SELECT 1 / 0;
\endif
SELECT length(:'management_password')>0 AS password_valid
\gset
\if :password_valid
\else
  \echo 'Management password is required'
  SELECT 1 / 0;
\endif

BEGIN;
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
              :'management_user', :'management_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:'management_user')
\gexec
-- Existing unsafe roles are rejected by Python provisioning, not silently repurposed.
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'management_user')
\gexec
COMMIT;
