\set ON_ERROR_STOP on
\getenv migration_user DB_MIGRATION_USER
\getenv migration_password DB_MIGRATION_PASSWORD
\getenv app_user DB_APP_USER
\getenv app_password DB_APP_PASSWORD
\getenv writer_user SOURCE_WRITER_USER
\getenv writer_password SOURCE_WRITER_PASSWORD
\getenv index_builder_user KNOWLEDGE_INDEX_BUILDER_USER
\getenv index_builder_password KNOWLEDGE_INDEX_BUILDER_PASSWORD
\getenv account_withdrawal_cleanup_user ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE
\getenv account_withdrawal_cleanup_password ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD
\getenv catalog_writer_user CATALOG_WRITER_USER
\getenv catalog_writer_password CATALOG_WRITER_PASSWORD
\getenv catalog_approval_user CATALOG_APPROVAL_USER
\getenv catalog_approval_password CATALOG_APPROVAL_PASSWORD
\getenv candidate_builder_user CANDIDATE_INDEX_BUILDER_USER
\getenv candidate_builder_password CANDIDATE_INDEX_BUILDER_PASSWORD
\if :{?index_builder_user}
\else
  \set index_builder_user ''
\endif
\if :{?index_builder_password}
\else
  \set index_builder_password ''
\endif
\if :{?account_withdrawal_cleanup_user}
\else
  \set account_withdrawal_cleanup_user ''
\endif
\if :{?account_withdrawal_cleanup_password}
\else
  \set account_withdrawal_cleanup_password ''
\endif
\if :{?catalog_writer_user}
\else
  \set catalog_writer_user ''
\endif
\if :{?catalog_writer_password}
\else
  \set catalog_writer_password ''
\endif
\if :{?catalog_approval_user}
\else
  \set catalog_approval_user ''
\endif
\if :{?catalog_approval_password}
\else
  \set catalog_approval_password ''
\endif
\if :{?candidate_builder_user}
\else
  \set candidate_builder_user ''
\endif
\if :{?candidate_builder_password}
\else
  \set candidate_builder_password ''
\endif

-- Alembic은 제한된 Migration 역할로 실행되므로 trusted 여부와 무관하게 필요한
-- extension을 Bootstrap/admin 단계에서 먼저 준비합니다. 이미 설치된 DB에도 안전하게 재실행됩니다.
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion = '0.8.6' AS vector_version_valid
FROM pg_extension
WHERE extname = 'vector'
\gset
\if :vector_version_valid
\else
  \echo 'pgvector extension version must be 0.8.6'
  SELECT 1 / 0;
\endif

-- Builder/Writer는 각 도메인 작업 전까지 생략할 수 있다. 한 값만 설정하거나 기존 역할과 충돌하면
-- 관리 계정 변경을 포함한 어떤 변경도 하지 않는다.
SELECT
  count(DISTINCT name) FILTER (WHERE length(name)>0)
    = 4 + CASE WHEN length(:'index_builder_user')>0 THEN 1 ELSE 0 END
        + CASE WHEN length(:'account_withdrawal_cleanup_user')>0 THEN 1 ELSE 0 END
        + CASE WHEN length(:'catalog_writer_user')>0 THEN 1 ELSE 0 END
        + CASE WHEN length(:'catalog_approval_user')>0 THEN 1 ELSE 0 END
        + CASE WHEN length(:'candidate_builder_user')>0 THEN 1 ELSE 0 END
  AND bool_and(length(name)>0)
    FILTER (WHERE name NOT IN (:'index_builder_user', :'account_withdrawal_cleanup_user', :'catalog_writer_user', :'catalog_approval_user', :'candidate_builder_user'))
  AND (length(:'index_builder_user')=0) = (length(:'index_builder_password')=0)
  AND (length(:'account_withdrawal_cleanup_user')=0) = (length(:'account_withdrawal_cleanup_password')=0)
  AND (length(:'catalog_writer_user')=0) = (length(:'catalog_writer_password')=0)
  AND (length(:'catalog_approval_user')=0) = (length(:'catalog_approval_password')=0)
  AND (length(:'candidate_builder_user')=0) = (length(:'candidate_builder_password')=0)
  AS roles_valid
FROM (VALUES (current_user), (:'migration_user'), (:'app_user'), (:'writer_user'),
             (:'index_builder_user'), (:'account_withdrawal_cleanup_user'),
             (:'catalog_writer_user'), (:'catalog_approval_user'),
             (:'candidate_builder_user')) AS roles(name)
\gset
\if :roles_valid
\else
  \echo 'Admin, Migration, Runtime, Writer and optional Index Builder/Cleanup/Catalog/Approval/Candidate roles must be distinct'
  -- SQL 오류로 ON_ERROR_STOP을 발동합니다 (psql 17의 \quit는 종료 코드를 받지 않음).
  SELECT 1 / 0;
\endif

BEGIN;
-- Bootstrap은 로그인 계정과 DDL 경계만 준비합니다. 테이블 권한은 migration 이후
-- Python provisioning이 명시적 목록으로 부여하며, 재실행 시 DML을 다시 열지 않습니다.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', name, password)
FROM (VALUES (:'migration_user', :'migration_password'),
             (:'app_user', :'app_password'), (:'writer_user', :'writer_password'),
             (:'index_builder_user', :'index_builder_password'),
             (:'account_withdrawal_cleanup_user', :'account_withdrawal_cleanup_password'),
             (:'catalog_writer_user', :'catalog_writer_password'),
             (:'catalog_approval_user', :'catalog_approval_password'),
             (:'candidate_builder_user', :'candidate_builder_password')) AS roles(name, password)
WHERE length(name)>0 AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=name)
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', name, password)
FROM (VALUES (:'migration_user', :'migration_password'),
             (:'app_user', :'app_password'), (:'writer_user', :'writer_password'),
             (:'index_builder_user', :'index_builder_password'),
             (:'account_withdrawal_cleanup_user', :'account_withdrawal_cleanup_password'),
             (:'catalog_writer_user', :'catalog_writer_password'),
             (:'catalog_approval_user', :'catalog_approval_password'),
             (:'candidate_builder_user', :'candidate_builder_password')) AS roles(name, password)
WHERE length(name)>0
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), name)
FROM (VALUES (:'migration_user'), (:'app_user'), (:'writer_user'), (:'index_builder_user'),
             (:'account_withdrawal_cleanup_user'), (:'catalog_writer_user'),
             (:'catalog_approval_user'),
             (:'candidate_builder_user')) AS roles(name)
WHERE length(name)>0
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'migration_user')
\gexec
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', name)
FROM (VALUES (:'app_user'), (:'writer_user'), (:'index_builder_user'),
             (:'account_withdrawal_cleanup_user'), (:'catalog_writer_user'),
             (:'catalog_approval_user'),
             (:'candidate_builder_user')) AS roles(name)
WHERE length(name)>0
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', name)
FROM (VALUES (:'app_user'), (:'writer_user'), (:'index_builder_user'),
             (:'account_withdrawal_cleanup_user'), (:'catalog_writer_user'),
             (:'catalog_approval_user'),
             (:'candidate_builder_user')) AS roles(name)
WHERE length(name)>0
\gexec

-- 이전 테이블·sequence 기본 권한을 global/schema 양쪽에서 회수합니다.
-- 기존 테이블 ACL은 확장하지 않고 migration 이후 명시적으로 재구성합니다.
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I %s REVOKE ALL ON %s FROM PUBLIC, %I, %I',
              :'migration_user', scope, object_type, :'app_user', :'writer_user')
FROM (VALUES (''), ('IN SCHEMA public')) AS scopes(scope)
CROSS JOIN (VALUES ('TABLES'), ('SEQUENCES')) AS objects(object_type)
\gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I %s REVOKE ALL ON %s FROM %I',
              :'migration_user', scope, object_type, name)
FROM (VALUES (''), ('IN SCHEMA public')) AS scopes(scope)
CROSS JOIN (VALUES ('TABLES'), ('SEQUENCES')) AS objects(object_type)
CROSS JOIN (VALUES (:'index_builder_user'), (:'account_withdrawal_cleanup_user'),
                   (:'catalog_writer_user'), (:'catalog_approval_user'),
                   (:'candidate_builder_user')) AS roles(name)
WHERE length(name)>0
\gexec
COMMIT;
