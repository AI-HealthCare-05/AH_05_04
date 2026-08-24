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

if [ -d "./app/tests" ] && find ./app/tests -name 'test_*.py' -print -quit | grep -q .; then
  HAS_TESTS=true
fi

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

echo "PostgreSQL container found. Preparing test database."

# test DB가 없을 때만 생성합니다.
# 애플리케이션 DB와 분리된 DB이므로 pytest의 테이블 생성·삭제가 이관 데이터에 영향을 주지 않습니다.
docker compose \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT format(
    'CREATE DATABASE %I OWNER %I',
    'test',
    current_user
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE datname = 'test'
)
\gexec
SQL

echo "Run Pytest with Coverage"

if ! uv run coverage run -m pytest app tests/contract; then
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
