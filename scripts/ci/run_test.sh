#!/usr/bin/env bash

# 오류, 미정의 변수 및 pipeline 실패가 발생하면 즉시 중단합니다.
set -euo pipefail

# 저장소 루트에서 명령이 실행되도록 이동합니다.
cd "$(dirname "$0")/../.."
REPOSITORY_ROOT="$(pwd)"

# shellcheck source=scripts/ci/test_environment.sh
source scripts/ci/test_environment.sh

echo "Find Tests"

HAS_TESTS=false

# 실제 기본 테스트 실행 범위와 동일한 디렉터리를 확인합니다.
for test_dir in \
  ./backend/app/tests \
  ./tests/contract \
  ./tests/migration \
  ./ai_worker/tests/core \
  ./ai_worker/tests/ocr \
  ./ai_worker/tests/rag \
  ./tests/integration; do
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

prepare_test_environment

echo "Apply Alembic migrations to test database"

run_with_backend_test_database alembic -c backend/alembic.ini upgrade head

echo "Validate migrated PostgreSQL schema"

run_with_backend_test_database pytest tests/migration -v

echo "Run Pytest with Coverage"

# Backend, 공통 계약, PostgreSQL·Redis 통합 테스트는 backend/app import 경로가
# 필요한 프로세스에서 실행합니다.
if ! run_with_backend_test_database \
  coverage run -m pytest \
  backend/app \
  tests/contract \
  tests/integration/rag \
  tests/integration/test_worker_ocr_persistence.py \
  tests/integration/test_outbox_publisher.py \
  tests/integration/test_worker_job_execution_repository.py::test_handler_permanent_failure_marks_real_ocr_job_failed \
  tests/integration/test_worker_job_execution_repository.py::test_worker_runtime_completes_real_redis_postgresql_ocr_one_cycle \
  tests/integration/test_worker_dlq_outbox_repository.py \
  tests/integration/test_worker_recovery_repository.py; then
  echo
  echo "Pytest failed."
  echo "Fix the test failures above and re-run."
  exit 1
fi

# ai_worker 단위 테스트는 backend/app을 PYTHONPATH에서 제외한 별도 프로세스로
# 실행하여 Worker가 Backend 내부 모듈에 의존하는 실수를 CI에서 잡습니다.
if ! run_with_worker_test_environment \
  coverage run --append -m pytest \
  ai_worker/tests/core \
  ai_worker/tests/ocr \
  ai_worker/tests/rag; then
  echo
  echo "AI Worker pytest failed."
  echo "Fix the test failures above and re-run."
  exit 1
fi

echo "Coverage Report"

if ! run_with_backend_test_database coverage report -m; then
  echo "Coverage check failed."
  exit 1
fi
