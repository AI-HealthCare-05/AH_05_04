#!/usr/bin/env bash

# 오류, 미정의 변수 및 pipeline 실패가 발생하면 즉시 중단합니다.
set -euo pipefail

# 저장소 루트에서 명령이 실행되도록 이동합니다.
cd "$(dirname "$0")/../.."

ENV_FILE="${ENV_FILE:-envs/.local.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

if [ ! -f "$ENV_FILE" ]; then
  echo "환경 파일을 찾을 수 없습니다: $ENV_FILE"
  exit 1
fi

echo "Find Tests"

HAS_TESTS=false

# 실제 기본 테스트 실행 범위와 동일한 디렉터리를 확인합니다.
for test_dir in ./app/tests ./tests/contract ./ai_worker/tests/core; do
  if [ -d "$test_dir" ] &&
    find "$test_dir" -type f -name 'test_*.py' -print -quit | grep -q .; then
    HAS_TESTS=true
    break
  fi
done

echo "Has tests: $HAS_TESTS"

if [ "$HAS_TESTS" != true ]; then
  echo "No tests found. Skipping tests."
  exit 0
fi

# Compose 프로젝트의 PostgreSQL 서비스가 실제 실행 중인지 확인합니다.
if ! docker compose \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  ps --services --status running |
  grep -qx postgres; then
  echo "PostgreSQL container not found."
  echo "Run: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d postgres"
  exit 1
fi

echo "PostgreSQL container found. Recreating isolated test database."

# 이전 일반 테스트가 애플리케이션 테이블만 삭제하고 alembic_version을
# 남겼을 수 있으므로 test DB를 매 실행마다 새로 생성합니다.
# 삭제 대상은 개발 DB와 분리된 로컬 test DB로 한정합니다.
docker compose \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres' <<'SQL'
DROP DATABASE IF EXISTS test WITH (FORCE);

SELECT format(
    'CREATE DATABASE %I OWNER %I',
    'test',
    current_user
)
\gexec
SQL

echo "Apply Alembic migrations to test database"

DB_HOST=127.0.0.1 \
DB_PORT="${DB_EXPOSE_PORT:-5432}" \
DB_EXPOSE_PORT="${DB_EXPOSE_PORT:-5432}" \
DB_NAME=test \
uv run alembic upgrade head

echo "Validate migrated PostgreSQL schema"

DB_HOST=127.0.0.1 \
DB_PORT="${DB_EXPOSE_PORT:-5432}" \
DB_EXPOSE_PORT="${DB_EXPOSE_PORT:-5432}" \
DB_NAME=test \
uv run pytest tests/migration -v

echo "Run Pytest with Coverage"

uv run coverage run -m pytest \
  app \
  tests/contract \
  ai_worker/tests/core

# Backend, 공통 계약, Worker 공통 골격 테스트를 모두 실행합니다.
if ! uv run coverage run -m pytest app tests/contract ai_worker/tests/core; then
  echo
  echo "Pytest failed."
  echo "Fix the test failures above and re-run."
  exit 1
fi

echo "Coverage Report"

if ! uv run coverage report -m; then
  echo "Coverage check failed."
  exit 1
fi
