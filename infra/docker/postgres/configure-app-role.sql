\set ON_ERROR_STOP on

-- 비밀번호를 SQL 파일이나 명령행 인수에 직접 기록하지 않고
-- PostgreSQL 컨테이너 환경변수에서 읽습니다.
\getenv migration_user DB_MIGRATION_USER
\getenv migration_password DB_MIGRATION_PASSWORD
\getenv app_user DB_APP_USER
\getenv app_password DB_APP_PASSWORD

-- Alembic 전용 Migration 역할을 생성합니다.
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'migration_user',
    :'migration_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'migration_user'
)
\gexec

-- 기존 Migration 역할도 최소 권한 정책과 현재 비밀번호로 정렬합니다.
SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
    :'migration_user',
    :'migration_password'
)
\gexec

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'migration_user'
)
\gexec

-- Alembic이 public schema의 객체를 생성할 수 있도록 허용합니다.
SELECT format(
    'GRANT USAGE, CREATE ON SCHEMA public TO %I',
    :'migration_user'
)
\gexec

-- FastAPI와 Worker가 사용할 Runtime 역할을 생성합니다.
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'app_user'
)
\gexec

-- Runtime 역할에는 관리·DDL 권한을 부여하지 않습니다.
SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
    :'app_user',
    :'app_password'
)
\gexec

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'app_user'
)
\gexec

SELECT format(
    'GRANT USAGE ON SCHEMA public TO %I',
    :'app_user'
)
\gexec

SELECT format(
    'REVOKE CREATE ON SCHEMA public FROM %I',
    :'app_user'
)
\gexec

-- 이미 존재하는 애플리케이션 테이블에는 DML 권한만 부여합니다.
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE
     ON ALL TABLES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec

-- PR #72 또는 이전 배포에서 부여됐을 수 있는 sequence UPDATE 권한을
-- 명시적으로 회수하여 setval() 사용을 차단합니다.
SELECT format(
    'REVOKE UPDATE
     ON ALL SEQUENCES IN SCHEMA public FROM %I',
    :'app_user'
)
\gexec

-- identity/serial 값 생성을 위한 권한만 부여합니다.
SELECT format(
    'GRANT USAGE, SELECT
     ON ALL SEQUENCES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec

-- 이후 Migration 역할이 생성할 테이블에도 Runtime DML 권한을 적용합니다.
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec

-- 기존 default privilege에 sequence UPDATE가 설정됐을 가능성도 제거합니다.
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public
     REVOKE UPDATE ON SEQUENCES FROM %I',
    :'migration_user',
    :'app_user'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public
     GRANT USAGE, SELECT ON SEQUENCES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec
