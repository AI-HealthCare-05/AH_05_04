#!/usr/bin/env bash

# Local PostgreSQL·Redis test runners share this file. Callers must set
# REPOSITORY_ROOT before sourcing it and then call prepare_test_environment.

ENV_FILE="${ENV_FILE:-envs/.local.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENVIRONMENT_ERROR_EXIT_CODE="${ENVIRONMENT_ERROR_EXIT_CODE:-1}"
TEST_DATABASE_NAME="test"
TEST_SERVICE_READY_ATTEMPTS=15
TEST_SERVICE_READY_INTERVAL_SECONDS=2
# 상속된 경로를 정리 대상으로 오인하지 않도록 현재 프로세스의 소유 상태를 초기화합니다.
TEST_STORAGE_DIR=""
TEST_STORAGE_DIR_OWNED=false
TEST_RUNNER_STATE_DIR=""
TEST_RUNNER_STATE_DIR_OWNED=false

test_environment_error() {
  echo
  echo "[ENVIRONMENT ERROR] $1"
  exit "$ENVIRONMENT_ERROR_EXIT_CODE"
}

cleanup_test_environment() {
  local original_status="$?"

  if [ "$TEST_STORAGE_DIR_OWNED" = true ] && [ -n "$TEST_STORAGE_DIR" ] && [ -d "$TEST_STORAGE_DIR" ]; then
    rm -rf -- "$TEST_STORAGE_DIR"
  fi

  if [ "$TEST_RUNNER_STATE_DIR_OWNED" = true ] && [ -n "$TEST_RUNNER_STATE_DIR" ] && [ -d "$TEST_RUNNER_STATE_DIR" ]; then
    rm -rf -- "$TEST_RUNNER_STATE_DIR"
  fi

  return "$original_status"
}

_exit_test_environment_on_signal() {
  exit "$1"
}

install_test_environment_cleanup_traps() {
  trap cleanup_test_environment EXIT
  trap '_exit_test_environment_on_signal 129' HUP
  trap '_exit_test_environment_on_signal 130' INT
  trap '_exit_test_environment_on_signal 143' TERM
}

prepare_test_runner_state_directory() {
  install_test_environment_cleanup_traps
  TEST_RUNNER_STATE_DIR_OWNED=true
  TEST_RUNNER_STATE_DIR="$(
    trap '' HUP INT TERM
    exec mktemp -d
  )"
}

prepare_test_storage_directory() {
  install_test_environment_cleanup_traps
  TEST_STORAGE_DIR_OWNED=true
  TEST_STORAGE_DIR="$(
    trap '' HUP INT TERM
    exec mktemp -d
  )"
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
    PYTEST_ADDOPTS= \
    uv run --env-file "$ENV_FILE" "$@"
}

run_with_worker_test_environment() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    DB_HOST=127.0.0.1 \
    DB_PORT=1 \
    DB_EXPOSE_PORT=1 \
    DB_NAME=worker_unit_tests_must_not_use_database \
    PYTHONPATH="$REPOSITORY_ROOT" \
    STORAGE_DIR="$TEST_STORAGE_DIR" \
    RELEASE_VALIDATION_ALLOWED=false \
    OCR_STRUCTURE_LLM_ENABLED=false \
    PYTEST_ADDOPTS= \
    uv run --env-file "$ENV_FILE" "$@"
}

run_with_integration_test_environment() {
  env \
    -u DB_USER \
    -u DB_PASSWORD \
    -u REDIS_PASSWORD \
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
    PYTEST_ADDOPTS= \
    uv run --env-file "$ENV_FILE" "$@"
}

wait_for_postgres() {
  local attempt
  local postgres_ready

  for ((attempt = 1; attempt <= TEST_SERVICE_READY_ATTEMPTS; attempt += 1)); do
    postgres_ready="$(
      docker compose \
        --env-file "$ENV_FILE" \
        -f "$COMPOSE_FILE" \
        exec -T postgres \
        sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null || true
    )"
    if [[ "$postgres_ready" == *"accepting connections"* ]]; then
      return 0
    fi

    if [ "$attempt" -lt "$TEST_SERVICE_READY_ATTEMPTS" ]; then
      sleep "$TEST_SERVICE_READY_INTERVAL_SECONDS"
    fi
  done

  return 1
}

wait_for_redis() {
  local attempt
  local redis_ready

  for ((attempt = 1; attempt <= TEST_SERVICE_READY_ATTEMPTS; attempt += 1)); do
    redis_ready="$(
      docker compose \
        --env-file "$ENV_FILE" \
        -f "$COMPOSE_FILE" \
        exec -T redis redis-cli ping 2>/dev/null || true
    )"
    if [ "$redis_ready" = "PONG" ]; then
      return 0
    fi

    if [ "$attempt" -lt "$TEST_SERVICE_READY_ATTEMPTS" ]; then
      sleep "$TEST_SERVICE_READY_INTERVAL_SECONDS"
    fi
  done

  return 1
}

prepare_test_environment() {
  local env_file_lower
  local compose_file_lower
  local selected_env
  local selected_env_lower

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
        gsub(/^[[:space:]"]+/, "", value)
        gsub(/[[:space:]"]+$/, "", value)
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

  if ! wait_for_postgres; then
    test_environment_error "PostgreSQL이 30초 안에 연결 준비를 마치지 못했습니다. Compose 로그를 확인해 주세요."
  fi

  if ! wait_for_redis; then
    test_environment_error "Redis가 30초 안에 연결 준비를 마치지 못했습니다. Compose 로그를 확인해 주세요."
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

  ISSUE398_TEST_POSTGRES_CONTAINER="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q postgres)"
  if [ -z "$ISSUE398_TEST_POSTGRES_CONTAINER" ]; then
    test_environment_error "Source 권한 검증용 PostgreSQL 컨테이너를 확인할 수 없습니다."
  fi
  export ISSUE398_TEST_POSTGRES_CONTAINER

  prepare_test_storage_directory
}
