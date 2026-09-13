\set ON_ERROR_STOP on
\getenv migration_user DB_MIGRATION_USER
\getenv migration_password DB_MIGRATION_PASSWORD
\getenv app_user DB_APP_USER
\getenv app_password DB_APP_PASSWORD
\getenv writer_user SOURCE_WRITER_USER
\getenv writer_password SOURCE_WRITER_PASSWORD

-- 계정 이름 충돌 시 관리 계정 변경을 포함한 어떤 변경도 하지 않습니다.
SELECT count(DISTINCT name)=4 AND bool_and(length(name)>0) AS roles_valid
FROM (VALUES (current_user), (:'migration_user'), (:'app_user'), (:'writer_user')) AS roles(name)
\gset
\if :roles_valid
\else
  \echo 'Admin, Migration, Runtime and Writer roles must be distinct and nonempty'
  -- SQL 오류로 ON_ERROR_STOP을 발동합니다 (psql 17의 \quit는 종료 코드를 받지 않음).
  SELECT 1 / 0;
\endif

BEGIN;
-- Bootstrap은 로그인 계정과 DDL 경계만 준비합니다. 테이블 권한은 migration 이후
-- Python provisioning이 명시적 목록으로 부여하며, 재실행 시 DML을 다시 열지 않습니다.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', name, password)
FROM (VALUES (:'migration_user', :'migration_password'),
             (:'app_user', :'app_password'), (:'writer_user', :'writer_password')) AS roles(name, password)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=name)
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', name, password)
FROM (VALUES (:'migration_user', :'migration_password'),
             (:'app_user', :'app_password'), (:'writer_user', :'writer_password')) AS roles(name, password)
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), name)
FROM (VALUES (:'migration_user'), (:'app_user'), (:'writer_user')) AS roles(name)
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'migration_user')
\gexec
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', name)
FROM (VALUES (:'app_user'), (:'writer_user')) AS roles(name)
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', name)
FROM (VALUES (:'app_user'), (:'writer_user')) AS roles(name)
\gexec

-- 이전 테이블·sequence 기본 권한을 global/schema 양쪽에서 회수합니다.
-- 기존 테이블 ACL은 확장하지 않고 migration 이후 명시적으로 재구성합니다.
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I %s REVOKE ALL ON %s FROM PUBLIC, %I, %I',
              :'migration_user', scope, object_type, :'app_user', :'writer_user')
FROM (VALUES (''), ('IN SCHEMA public')) AS scopes(scope)
CROSS JOIN (VALUES ('TABLES'), ('SEQUENCES')) AS objects(object_type)
\gexec
COMMIT;
