#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/../.."
REPOSITORY_ROOT="$(pwd)"

# 0=success, 1=test/migration failure, 2=local environment is not ready.
ENVIRONMENT_ERROR_EXIT_CODE=2

# shellcheck source=scripts/ci/test_environment.sh
source scripts/ci/test_environment.sh

if ! find tests/integration -type f -name 'test_*.py' -print -quit | grep -q .; then
  test_environment_error "tests/integration에서 실행할 테스트를 찾지 못했습니다."
fi

prepare_test_environment

echo "Alembic migration을 test 데이터베이스에 적용합니다."
if ! run_with_integration_test_environment alembic -c backend/alembic.ini upgrade head; then
  echo
  echo "[TEST FAILURE] Alembic migration 적용에 실패했습니다."
  exit 1
fi

echo "tests/integration 전체를 실행합니다."
if ! run_with_integration_test_environment pytest tests/integration -q; then
  echo
  echo "[TEST FAILURE] 통합테스트가 실패했습니다. 위 pytest 결과를 확인해 주세요."
  exit 1
fi

echo
echo "[SUCCESS] tests/integration 전체가 통과했습니다."
