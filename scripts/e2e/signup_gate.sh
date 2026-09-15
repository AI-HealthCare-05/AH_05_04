#!/usr/bin/env bash
set -euo pipefail

# No existing env file, database, SMTP account, or Provider is used by this runner.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_ROOT="${AUTH_GATE_BACKEND_ROOT:-$REPO_ROOT}"
PYTHON_BIN="${AUTH_GATE_PYTHON:-$BACKEND_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" || ! -f "$BACKEND_ROOT/backend/alembic.ini" ]]; then
  echo 'Set AUTH_GATE_BACKEND_ROOT to the #549 checkout and AUTH_GATE_PYTHON to its dependency runtime.' >&2
  exit 2
fi
BACKEND_ROOT="$(cd "$BACKEND_ROOT" && pwd)"
PYTHON_BIN="$(cd "$(dirname "$PYTHON_BIN")" && pwd)/$(basename "$PYTHON_BIN")"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dosey-signup-gate.XXXXXXXX")"
DB_CONTAINER="dosey-signup-gate-$(basename "$RUN_DIR" | tr '.' '-')"
DB_PASSWORD_VALUE="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(24))')"
DB_PORT_VALUE=0
GATE_ON_PID=''
GATE_OFF_PID=''
cleanup() {
  for pid in "$GATE_ON_PID" "$GATE_OFF_PID"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  done
  docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backend_env() {
  local command=(env -i PATH="$PATH" PYTHONPATH="$BACKEND_ROOT/backend:$BACKEND_ROOT" \
    ENV=local EMAIL_PROVIDER=noop OPENAI_API_KEY=sk-not-configured \
    DB_HOST=127.0.0.1 DB_PORT="$DB_PORT_VALUE" DB_EXPOSE_PORT="$DB_PORT_VALUE" \
    DB_NAME=signup_gate_e2e DB_USER=signup_e2e DB_PASSWORD="$DB_PASSWORD_VALUE" \
    STORAGE_DIR="$RUN_DIR/uploads" CORS_ALLOWED_ORIGINS=http://127.0.0.1:18433 \
    OCR_CONSENT_POLICY_VERSION=synthetic-ocr-e2e \
    SIGNUP_EMAIL_VERIFICATION_REQUIRED=true)
  if [[ "${1:-}" == '--exec' ]]; then
    shift
    exec "${command[@]}" "$@"
  else
    "${command[@]}" "$@"
  fi
}

# Config loads .env from cwd. Use a fresh directory so even a developer's local .env is excluded.
cd "$RUN_DIR"
backend_env "$PYTHON_BIN" -c '
from app.core import config
if getattr(config, "SIGNUP_EMAIL_VERIFICATION_REQUIRED", None) is not True:
    raise SystemExit("Backend does not implement #549. Set AUTH_GATE_BACKEND_ROOT to its checkout.")
'
# Bind checks prevent readiness probes from accidentally using another task's server.
"$PYTHON_BIN" -c '
import socket
sockets = []
for port in (18432, 18433, 18434):
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sockets.append(sock)
'
POSTGRES_PASSWORD="$DB_PASSWORD_VALUE" docker run -d --name "$DB_CONTAINER" \
  -e POSTGRES_PASSWORD -e POSTGRES_USER=signup_e2e -e POSTGRES_DB=signup_gate_e2e \
  --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 \
  pgvector/pgvector:0.8.6-pg17-bookworm >/dev/null
DB_PORT_VALUE="$(docker port "$DB_CONTAINER" 5432/tcp | sed 's/127.0.0.1://')"
ready=false
for ((attempt=0; attempt<30; attempt++)); do
  if docker exec "$DB_CONTAINER" pg_isready -U signup_e2e -d signup_gate_e2e >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
if [[ "$ready" != true ]]; then echo 'Isolated PostgreSQL did not become ready.' >&2; exit 2; fi
backend_env "$PYTHON_BIN" -m alembic -c "$BACKEND_ROOT/backend/alembic.ini" upgrade head >"$RUN_DIR/migrate.log" 2>&1 || {
  echo 'Isolated migration failed.' >&2; tail -20 "$RUN_DIR/migrate.log" >&2; exit 1;
}
backend_env --exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 18432 --no-access-log >"$RUN_DIR/on.log" 2>&1 &
GATE_ON_PID=$!
backend_env --exec env SIGNUP_EMAIL_VERIFICATION_REQUIRED=false "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 18434 --no-access-log >"$RUN_DIR/off.log" 2>&1 &
GATE_OFF_PID=$!
for port in 18432 18434; do
  ready=false
  for ((attempt=0; attempt<30; attempt++)); do
    if ! kill -0 "$GATE_ON_PID" 2>/dev/null || ! kill -0 "$GATE_OFF_PID" 2>/dev/null; then
      echo 'An isolated Backend process exited before readiness.' >&2; exit 2
    fi
    if curl -fsS --connect-timeout 1 --max-time 2 "http://127.0.0.1:$port/api/openapi.json" >/dev/null 2>&1; then ready=true; break; fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then echo "Isolated Backend on port $port did not become ready." >&2; exit 2; fi
done
echo "Backend revision: $(git -C "$BACKEND_ROOT" rev-parse --short HEAD)"
echo 'Local FastAPI + migrated temporary PostgreSQL; SMTP/AI calls disabled.'
cd "$REPO_ROOT/frontend"
SIGNUP_GATE_E2E=1 SIGNUP_GATE_DB_CONTAINER="$DB_CONTAINER" \
  pnpm exec playwright test --config playwright.auth-gate.config.ts
