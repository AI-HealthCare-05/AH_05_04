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

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Compose 파일을 찾을 수 없습니다: $COMPOSE_FILE"
  exit 1
fi

# test DB를 삭제하고 다시 만드는 스크립트이므로
# production 환경파일이나 Production Compose에서는 실행하지 않습니다.
ENV_FILE_LOWER="$(
  printf '%s' "$ENV_FILE" |
    tr '[:upper:]' '[:lower:]'
)"

COMPOSE_FILE_LOWER="$(
  printf '%s' "$COMPOSE_FILE" |
    tr '[:upper:]' '[:lower:]'
)"

# Production Compose를 해석하기 전에 파일명 기준으로 먼저 차단합니다.
if [[ "$ENV_FILE_LOWER" == *prod* ]] ||
  [[ "$COMPOSE_FILE_LOWER" == *prod* ]]; then
  echo "Production 환경에서는 test DB 재생성 스크립트를 실행할 수 없습니다."
  echo "ENV_FILE=$ENV_FILE"
  echo "COMPOSE_FILE=$COMPOSE_FILE"
  exit 1
fi

# 선택한 환경파일의 ENV 값을 직접 읽습니다.
# Docker Compose 설정을 해석하기 전에 검사하므로 잘못된 운영 파일도 안전하게 차단합니다.
SELECTED_ENV="$(
  awk -F= '
    /^[[:space:]]*ENV[[:space:]]*=/ {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]"]+/, "", value)
      gsub(/[[:space:]"]+$/, "", value)
      print value
    }
  ' "$ENV_FILE" |
    tail -n 1
)"

SELECTED_ENV_LOWER="$(
  printf '%s' "$SELECTED_ENV" |
    tr '[:upper:]' '[:lower:]'
)"

# DB를 강제로 재생성하는 스크립트이므로 local 또는 test만 허용합니다.
if [ "$SELECTED_ENV_LOWER" != "local" ] &&
  [ "$SELECTED_ENV_LOWER" != "test" ]; then
  echo "이 스크립트는 local 또는 test 환경에서만 실행할 수 있습니다."
  echo "선택된 ENV=${SELECTED_ENV:-<empty>}"
  exit 1
fi

echo "Find Tests"

HAS_TESTS=false

# 실제 기본 테스트 실행 범위와 동일한 디렉터리를 확인합니다.
for test_dir in \
  ./app/tests \
  ./tests/contract \
  ./tests/migration \
  ./ai_worker/tests/core; do
  if [ -d "$test_dir" ] &&
    find "$test_dir" -type f -name 'test_*.py' -print -quit |
      grep -q .; then
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

# 실제 Compose port mapping을 사용하여 호스트 접속 포트를 결정합니다.
HOST_DB_PORT="$(
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    port postgres 5432 |
    tail -n 1 |
    awk -F: '{print $NF}'
)"

if [[ ! "$HOST_DB_PORT" =~ ^[0-9]+$ ]]; then
  echo "PostgreSQL 호스트 포트를 확인할 수 없습니다: $HOST_DB_PORT"
  exit 1
fi

echo "PostgreSQL container found. Recreating isolated test database."

# 이전 일반 테스트가 애플리케이션 테이블만 삭제하고 alembic_version을
# 남겼을 수 있으므로 test DB를 매 실행마다 새로 생성합니다.
# 삭제 대상은 개발 DB와 분리된 literal test DB로 한정합니다.
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

# 기존 shell의 DB 계정은 제거하고, 선택한 ENV_FILE에서 DB_USER와
# DB_PASSWORD를 로딩합니다. 호스트·포트·DB 이름만 test DB 기준으로 덮어씁니다.
run_with_test_database() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    DB_HOST=127.0.0.1 \
    DB_PORT="$HOST_DB_PORT" \
    DB_EXPOSE_PORT="$HOST_DB_PORT" \
    DB_NAME=test \
    uv run --env-file "$ENV_FILE" "$@"
}

echo "Apply Alembic migrations to test database"

run_with_test_database alembic upgrade head

echo "Validate migrated PostgreSQL schema"

run_with_test_database pytest tests/migration -v

echo "Run Pytest with Coverage"

# Backend, 공통 계약, Worker 공통 골격 테스트를 한 번만 실행합니다.
if ! run_with_test_database \
  coverage run -m pytest \
  app \
  tests/contract \
  ai_worker/tests/core; then
  echo
  echo "Pytest failed."
  echo "Fix the test failures above and re-run."
  exit 1
fi

echo "Coverage Report"

if ! run_with_test_database coverage report -m; then
  echo "Coverage check failed."
  exit 1
fi
