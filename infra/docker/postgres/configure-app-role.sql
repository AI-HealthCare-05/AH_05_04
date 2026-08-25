\set ON_ERROR_STOP on

-- 비밀번호를 SQL 파일이나 명령행 인수에 직접 기록하지 않고
-- PostgreSQL 컨테이너 환경변수에서 읽습니다.
\getenv app_user DB_APP_USER
\getenv app_password DB_APP_PASSWORD
\getenv migration_user DB_MIGRATION_USER

-- 애플리케이션 계정이 없을 때만 생성합니다.
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

-- 기존 계정도 배포 시 지정된 비밀번호와 최소 권한 정책으로 정렬합니다.
SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
    :'app_user',
    :'app_password'
)
\gexec

-- FastAPI가 DB와 public schema를 사용할 수 있도록 허용합니다.
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

-- 이미 존재하는 애플리케이션 테이블과 sequence에 DML 권한만 부여합니다.
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec

SELECT format(
    'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec

-- 이후 Alembic이 생성하는 테이블과 sequence에도 같은 권한이 자동 적용됩니다.
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public
     GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec

-- 애플리케이션 계정에는 schema 객체 생성 권한을 주지 않습니다.
SELECT format(
    'REVOKE CREATE ON SCHEMA public FROM %I',
    :'app_user'
)
\gexec
