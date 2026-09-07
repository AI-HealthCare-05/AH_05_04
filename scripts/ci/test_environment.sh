#!/usr/bin/env bash

# Local PostgreSQL·Redis test runners share this file. Callers must set
# REPOSITORY_ROOT before sourcing it and then call prepare_test_environment.

ENV_FILE="${ENV_FILE:-envs/.local.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENVIRONMENT_ERROR_EXIT_CODE="${ENVIRONMENT_ERROR_EXIT_CODE:-1}"
TEST_DATABASE_NAME="test"

test_environment_error() {
  echo
  echo "[ENVIRONMENT ERROR] $1"
  exit "$ENVIRONMENT_ERROR_EXIT_CODE"
}

cleanup_test_environment() {
  local original_status="$?"

  if [ -n "${TEST_STORAGE_DIR:-}" ] && [ -d "$TEST_STORAGE_DIR" ]; then
    rm -rf -- "$TEST_STORAGE_DIR"
  fi

  return "$original_status"
}

run_with_backend_test_database() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    DB_HOST=127.0.0.1 \
    DB_PORT="$HOST_DB_PORT" \
    DB_EXPOSE_PORT="$HOST_DB_PORT" \
    DB_NAME="$TEST_DATABASE_NAME" \
    PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT" \
    STORAGE_DIR="$TEST_STORAGE_DIR" \
    RELEASE_VALIDATION_ALLOWED=false \
    OCR_STRUCTURE_LLM_ENABLED=false \
    uv run --env-file "$ENV_FILE" "$@"
}

run_with_worker_test_environment() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    DB_HOST=127.0.0.1 \
    DB_PORT="$HOST_DB_PORT" \
    DB_EXPOSE_PORT="$HOST_DB_PORT" \
    DB_NAME="$TEST_DATABASE_NAME" \
    PYTHONPATH="$REPOSITORY_ROOT" \
    STORAGE_DIR="$TEST_STORAGE_DIR" \
    RELEASE_VALIDATION_ALLOWED=false \
    OCR_STRUCTURE_LLM_ENABLED=false \
    uv run --env-file "$ENV_FILE" "$@"
}

run_with_integration_test_environment() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    -u TEST_REDIS_PASSWORD \
    DB_HOST=127.0.0.1 \
    DB_PORT="$HOST_DB_PORT" \
    DB_EXPOSE_PORT="$HOST_DB_PORT" \
    DB_NAME="$TEST_DATABASE_NAME" \
    REDIS_HOST=127.0.0.1 \
    REDIS_PORT="$HOST_REDIS_PORT" \
    TEST_REDIS_HOST=127.0.0.1 \
    TEST_REDIS_PORT="$HOST_REDIS_PORT" \
    PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT" \
    STORAGE_DIR="$TEST_STORAGE_DIR" \
    RELEASE_VALIDATION_ALLOWED=false \
    OCR_STRUCTURE_LLM_ENABLED=false \
    uv run --env-file "$ENV_FILE" "$@"
}

prepare_test_environment() {
  local env_file_lower
  local compose_file_lower
  local selected_env
  local selected_env_lower
  local postgres_ready
  local redis_ready

  if [ ! -f "$ENV_FILE" ]; then
    test_environment_error "환경 파일을 찾을 수 없습니다: $ENV_FILE"
  fi

  if [ ! -f "$COMPOSE_FILE" ]; then
    test_environment_error "Compose 파일을 찾을 수 없습니다: $COMPOSE_FILE"
  fi

  env_file_lower="$(printf '%s' "$ENV_FILE" | tr '[:upper:]' '[:lower:]')"
  compose_file_lower="$(printf '%s' "$COMPOSE_FILE" | tr '[:upper:]' '[:lower:]')"

  # Compose를 해석하기 전에 production으로 보이는 파일을 차단합니다.
  if [[ "$env_file_lower" == *prod* ]] ||
    [[ "$compose_file_lower" == *prod* ]]; then
    test_environment_error "Production 환경에서는 test DB 재생성 스크립트를 실행할 수 없습니다. ENV_FILE=$ENV_FILE COMPOSE_FILE=$COMPOSE_FILE"
  fi

  selected_env="$(
    awk -F= '
      /^[[:space:]]*ENV[[:space:]]*=/ {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]\"]+/, "", value)
        gsub(/[[:space:]\"]+$/, "", value)
        print value
      }
    ' "$ENV_FILE" |
      tail -n 1
  )"
  selected_env_lower="$(printf '%s' "$selected_env" | tr '[:upper:]' '[:lower:]')"

  if [ "$selected_env_lower" != "local" ] &&
    [ "$selected_env_lower" != "test" ]; then
    test_environment_error "이 스크립트는 local 또는 test 환경에서만 실행할 수 있습니다. 선택된 ENV=${selected_env:-<empty>}"
  fi

  if [ "$TEST_DATABASE_NAME" != "test" ]; then
    test_environment_error "삭제 가능한 데이터베이스 이름은 literal test뿐입니다."
  fi

  if ! command -v docker >/dev/null 2>&1; then
    test_environment_error "Docker를 찾을 수 없습니다. Docker Desktop을 설치하고 실행해 주세요."
  fi

  if ! docker compose version >/dev/null 2>&1; then
    test_environment_error "Docker Compose를 사용할 수 없습니다. Docker Desktop 상태를 확인해 주세요."
  fi

  if ! docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    ps --services --status running |
    grep -qx postgres; then
    test_environment_error "PostgreSQL이 실행 중이 아닙니다. 실행: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d postgres"
  fi

  if ! docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    ps --services --status running |
    grep -qx redis; then
    test_environment_error "Redis가 실행 중이 아닙니다. 실행: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d redis"
  fi

  postgres_ready="$(
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      exec -T postgres \
      sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null || true
  )"
  if [[ "$postgres_ready" != *"accepting connections"* ]]; then
    test_environment_error "PostgreSQL이 아직 연결을 받을 준비가 되지 않았습니다. 잠시 후 다시 실행해 주세요."
  fi

  redis_ready="$(
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      exec -T redis redis-cli ping 2>/dev/null || true
  )"
  if [ "$redis_ready" != "PONG" ]; then
    test_environment_error "Redis가 아직 연결을 받을 준비가 되지 않았습니다. 잠시 후 다시 실행해 주세요."
  fi

  HOST_DB_PORT="$(
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      port postgres 5432 2>/dev/null |
      tail -n 1 |
      awk -F: '{print $NF}'
  )"
  if [[ ! "$HOST_DB_PORT" =~ ^[0-9]+$ ]]; then
    test_environment_error "PostgreSQL 호스트 포트를 확인할 수 없습니다."
  fi

  HOST_REDIS_PORT="$(
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      port redis 6379 2>/dev/null |
      tail -n 1 |
      awk -F: '{print $NF}'
  )"
  if [[ ! "$HOST_REDIS_PORT" =~ ^[0-9]+$ ]]; then
    test_environment_error "Redis 호스트 포트를 확인할 수 없습니다."
  fi

  echo "PostgreSQL·Redis 준비 상태 확인 완료."
  echo "격리된 test 데이터베이스를 재생성합니다."

  if ! docker compose \
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
  then
    test_environment_error "격리된 test 데이터베이스를 재생성하지 못했습니다."
  fi

  TEST_STORAGE_DIR="$(mktemp -d)"
  trap cleanup_test_environment EXIT
}
