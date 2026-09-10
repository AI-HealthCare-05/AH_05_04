#!/usr/bin/env bash

# 오류, 미정의 변수 및 pipeline 실패가 발생하면 즉시 중단합니다.
set -euo pipefail

# 저장소 루트에서 명령이 실행되도록 이동합니다.
cd "$(dirname "$0")/../.."
REPOSITORY_ROOT="$(pwd)"

# shellcheck source=scripts/ci/test_environment.sh
source scripts/ci/test_environment.sh
# shellcheck source=scripts/ci/parallel_test_lanes.sh
source scripts/ci/parallel_test_lanes.sh

export PYTEST_ADDOPTS=""
uv run python scripts/ci/check_python_test_inventory.py
uv run python scripts/ci/check_protected_table_writes.py

prepare_test_environment
prepare_test_runner_state_directory
TEST_COVERAGE_DIR="$TEST_RUNNER_STATE_DIR/coverage"
PARALLEL_TEST_LOG_DIR="$TEST_RUNNER_STATE_DIR/test-lane-logs"
export PARALLEL_TEST_LOG_DIR
mkdir -p "$TEST_COVERAGE_DIR" "$TEST_RUNNER_STATE_DIR/pytest-cache/backend" "$TEST_RUNNER_STATE_DIR/pytest-cache/worker"

echo "Apply Alembic migrations to test database"

run_with_backend_test_database alembic -c backend/alembic.ini upgrade 398b2c3d4e5f

echo "Validate migrated PostgreSQL schema"

run_with_backend_test_database pytest tests/migration -v

# Historical downgrade tests finish before the irreversible Source cutover.
run_with_backend_test_database alembic -c backend/alembic.ini upgrade head
run_with_backend_test_database python scripts/ci/verify_database_head.py

run_backend_test_lane() {
  local cache_dir="$TEST_RUNNER_STATE_DIR/pytest-cache/backend"
  local COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage.backend"
  export COVERAGE_FILE

  echo "Run Backend, Contract and selected PostgreSQL tests with Coverage"

  if ! run_with_backend_test_database \
    coverage run -m pytest -o "cache_dir=$cache_dir" \
    backend/app \
    tests/contract \
    tests/integration/rag \
    tests/integration/test_worker_ocr_persistence.py; then
    echo
    echo "Backend pytest failed."
    echo "Fix the test failures above and re-run."
    return 1
  fi

  # 실제 Redis를 사용하는 선별 통합 테스트는 같은 test DB를 공유하므로 Backend lane 안에서
  # 직렬 실행합니다. Worker 단위 테스트의 승인 Redis 기본값은 별도 lane에서 유지합니다.
  if ! run_with_integration_test_environment \
    coverage run --append -m pytest -o "cache_dir=$cache_dir" \
    tests/integration/test_outbox_publisher.py \
    tests/integration/test_worker_job_execution_repository.py \
    tests/integration/test_worker_dlq_outbox_repository.py \
    tests/integration/test_worker_recovery_repository.py; then
    echo
    echo "Redis integration pytest failed."
    echo "Fix the test failures above and re-run."
    return 1
  fi
}

run_worker_test_lane() {
  local cache_dir="$TEST_RUNNER_STATE_DIR/pytest-cache/worker"
  local COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage.worker"
  export COVERAGE_FILE

  # ai_worker 단위 테스트는 backend/app을 PYTHONPATH에서 제외한 별도 프로세스로
  # 실행하여 Worker가 Backend 내부 모듈에 의존하는 실수를 잡습니다.
  echo "Run AI Worker tests with Coverage"

  if ! run_with_worker_test_environment \
    coverage run -m pytest -o "cache_dir=$cache_dir" \
    ai_worker/tests/core \
    ai_worker/tests/ocr \
    ai_worker/tests/rag \
    ai_worker/tests/evaluation; then
    echo
    echo "AI Worker pytest failed."
    echo "Fix the test failures above and re-run."
    return 1
  fi
}

echo "Run independent test lanes in parallel"

run_parallel_test_lanes_with_failure_summary run_backend_test_lane run_worker_test_lane

echo "Coverage Report"

COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage"
export COVERAGE_FILE

if ! run_with_backend_test_database coverage combine "$TEST_COVERAGE_DIR"; then
  echo "Coverage data combine failed."
  exit 1
fi

if ! run_with_backend_test_database coverage report -m; then
  echo "Coverage check failed."
  exit 1
fi
