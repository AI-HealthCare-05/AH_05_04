#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/../.."

ACTION="${1:-status}"
REAL_STACK_ENV_FILE="${REAL_STACK_ENV_FILE:-envs/.local.env}"
COMPOSE_FILE="docker-compose.real-stack-e2e.yml"
PROJECT_NAME="dosey-real-stack-e2e"

compose() {
  REAL_STACK_ENV_FILE="$REAL_STACK_ENV_FILE" docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$REAL_STACK_ENV_FILE" \
    -f "$COMPOSE_FILE" "$@"
}

require_env_file() {
  if [[ ! -f "$REAL_STACK_ENV_FILE" ]]; then
    echo "[REAL-STACK] $REAL_STACK_ENV_FILE 파일이 없습니다. envs/example.local.env를 기준으로 로컬 전용 값을 준비해 주세요." >&2
    exit 2
  fi

  case "$REAL_STACK_ENV_FILE" in
    *prod*|*staging*)
      echo "[REAL-STACK] production 또는 staging 환경파일은 사용할 수 없습니다." >&2
      exit 2
      ;;
  esac
}

require_live_ai_opt_in() {
  if [[ "${RUN_REAL_STACK_AI_E2E:-}" != "1" ]]; then
    echo "[REAL-STACK] 실제 CLOVA·OpenAI 호출에는 RUN_REAL_STACK_AI_E2E=1 명시가 필요합니다." >&2
    exit 2
  fi

  for key in CLOVA_OCR_INVOKE_URL CLOVA_OCR_SECRET OPENAI_API_KEY OPENAI_MODEL; do
    value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$REAL_STACK_ENV_FILE")"
    if [[ -z "$value" || "$value" == *replace-with-* || "$value" == *not-configured* || "$value" == *example.apigw* ]]; then
      echo "[REAL-STACK] $key 값이 없거나 placeholder입니다. 값 자체는 출력하지 않습니다." >&2
      exit 2
    fi
  done
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local attempts=60

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "[REAL-STACK] $label 준비를 확인하지 못했습니다: $url" >&2
  return 1
}

start_stack() {
  require_env_file
  compose up -d --build postgres redis migrate fastapi ai-worker frontend
  wait_for_url "FastAPI" "http://127.0.0.1:18000/api/openapi.json"
  wait_for_url "Frontend" "http://127.0.0.1:14173"
  echo "[REAL-STACK] 준비 완료: http://127.0.0.1:14173"
}

case "$ACTION" in
  up)
    start_stack
    ;;
  test)
    require_live_ai_opt_in
    start_stack
    cleanup() {
      if [[ "${KEEP_REAL_STACK:-}" != "1" ]]; then
        compose down --volumes --remove-orphans
      fi
    }
    trap cleanup EXIT
    (
      cd frontend
      REAL_STACK_WEB_URL=http://127.0.0.1:14173 \
        pnpm exec playwright test --config=playwright.real-stack.config.ts
    )
    ;;
  down)
    require_env_file
    compose down --volumes --remove-orphans
    ;;
  status)
    require_env_file
    compose ps
    ;;
  *)
    echo "사용법: bash scripts/e2e/real_stack.sh {up|test|status|down}" >&2
    exit 2
    ;;
esac
