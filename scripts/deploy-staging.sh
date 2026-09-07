#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/.."

STAGING_ENV_FILE="${STAGING_ENV_FILE:-envs/.staging.env}"
REMOTE_USER="ubuntu"
REMOTE_DIR="/home/ubuntu/finalproject-staging"
STAGING_COMPOSE_FILE="infra/docker/docker-compose.staging.yml"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

if [ ! -f "$STAGING_ENV_FILE" ]; then
  fail "Staging 환경파일을 찾을 수 없습니다: $STAGING_ENV_FILE"
fi

# Compose와 같은 값을 build·SSH 단계에서도 사용합니다. 실제 파일은 gitignore되며
# shell 구문이 아닌 KEY=VALUE 형식으로만 관리합니다.
set -a
# shellcheck disable=SC1090
source "$STAGING_ENV_FILE"
set +a

required_variables=(
  DOCKER_USER
  DOCKER_REPOSITORY
  APP_VERSION
  FRONTEND_VERSION
  STAGING_EC2_HOST
  STAGING_SSH_KEY_PATH
  STAGING_PUBLIC_ORIGIN
  ENV
  SECRET_KEY
  IDEMPOTENCY_HMAC_KEY
  CORS_ALLOWED_ORIGINS
  DB_NAME
  DB_ADMIN_USER
  DB_ADMIN_PASSWORD
  DB_MIGRATION_USER
  DB_MIGRATION_PASSWORD
  DB_APP_USER
  DB_APP_PASSWORD
  OPENAI_API_KEY
  CLOVA_OCR_INVOKE_URL
  CLOVA_OCR_SECRET
)

for variable_name in "${required_variables[@]}"; do
  if [ -z "${!variable_name:-}" ]; then
    fail "필수 Staging 환경변수가 비어 있습니다: $variable_name"
  fi
done

if grep -Eq '=(replace-with|replace_with)' "$STAGING_ENV_FILE"; then
  fail "$STAGING_ENV_FILE 안의 placeholder를 실제 Staging 값으로 교체해야 합니다."
fi

if [ "$ENV" != "staging" ]; then
  fail "ENV는 staging이어야 합니다. 현재 값: $ENV"
fi

if [ "${#SECRET_KEY}" -lt 32 ] || [ "${#IDEMPOTENCY_HMAC_KEY}" -lt 32 ]; then
  fail "SECRET_KEY와 IDEMPOTENCY_HMAC_KEY는 각각 32자 이상이어야 합니다."
fi

if [ "$DB_ADMIN_USER" = "$DB_MIGRATION_USER" ] ||
  [ "$DB_ADMIN_USER" = "$DB_APP_USER" ] ||
  [ "$DB_MIGRATION_USER" = "$DB_APP_USER" ]; then
  fail "DB_ADMIN_USER, DB_MIGRATION_USER, DB_APP_USER는 서로 달라야 합니다."
fi

if [[ ! "$APP_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] ||
  [[ ! "$FRONTEND_VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
  fail "이미지 version은 영문자, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다."
fi

if [ "$APP_VERSION" = "latest" ] || [ "$FRONTEND_VERSION" = "latest" ]; then
  fail "Rollback을 위해 latest 대신 commit SHA 또는 고정 version을 사용해야 합니다."
fi

if [[ ! "$DOCKER_USER" =~ ^[A-Za-z0-9._-]+$ ]] ||
  [[ ! "$DOCKER_REPOSITORY" =~ ^[A-Za-z0-9._-]+$ ]]; then
  fail "Docker Hub 사용자와 repository 이름의 형식이 올바르지 않습니다."
fi

if [[ ! "$STAGING_EC2_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
  fail "STAGING_EC2_HOST는 IPv4 주소 또는 안전한 hostname이어야 합니다."
fi

if [[ ! "$STAGING_PUBLIC_ORIGIN" =~ ^http://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  fail "현재 스크립트의 STAGING_PUBLIC_ORIGIN은 path가 없는 http origin이어야 합니다. HTTPS는 인증서 구성을 추가한 뒤 별도로 전환합니다."
fi

if [ "$CORS_ALLOWED_ORIGINS" != "$STAGING_PUBLIC_ORIGIN" ]; then
  fail "단일 origin Staging에서는 CORS_ALLOWED_ORIGINS와 STAGING_PUBLIC_ORIGIN이 같아야 합니다."
fi

if [[ "$STAGING_SSH_KEY_PATH" != /* ]] || [ ! -f "$STAGING_SSH_KEY_PATH" ]; then
  fail "STAGING_SSH_KEY_PATH는 존재하는 PEM 파일의 절대경로여야 합니다."
fi

command -v docker >/dev/null 2>&1 || fail "로컬에 Docker가 설치되어 있지 않습니다."
command -v ssh >/dev/null 2>&1 || fail "로컬에 ssh가 설치되어 있지 않습니다."
command -v scp >/dev/null 2>&1 || fail "로컬에 scp가 설치되어 있지 않습니다."

chmod 400 "$STAGING_SSH_KEY_PATH"
chmod 600 "$STAGING_ENV_FILE"

printf 'Docker Hub PAT을 입력하세요. 값은 파일이나 명령 인자에 저장하지 않습니다.\n'
read -r -s -p "Docker Hub PAT: " docker_registry_pat
printf '\n'

if [ -z "$docker_registry_pat" ]; then
  fail "Docker Hub PAT이 비어 있습니다."
fi

printf '%s' "$docker_registry_pat" |
  docker login -u "$DOCKER_USER" --password-stdin

backend_image="${DOCKER_USER}/${DOCKER_REPOSITORY}:app-${APP_VERSION}"
frontend_image="${DOCKER_USER}/${DOCKER_REPOSITORY}:frontend-${FRONTEND_VERSION}"

printf 'Building %s\n' "$backend_image"
docker build \
  --platform linux/amd64 \
  -t "$backend_image" \
  -f backend/app/Dockerfile \
  .

printf 'Building %s\n' "$frontend_image"
docker build \
  --platform linux/amd64 \
  --build-arg "VITE_API_BASE_URL=$STAGING_PUBLIC_ORIGIN" \
  -t "$frontend_image" \
  -f frontend/Dockerfile.prod \
  .

docker push "$backend_image"
docker push "$frontend_image"

ssh -i "$STAGING_SSH_KEY_PATH" "${REMOTE_USER}@${STAGING_EC2_HOST}" \
  "install -d -m 700 '$REMOTE_DIR' '$REMOTE_DIR/postgres' '$REMOTE_DIR/deployment-evidence'"

ssh -i "$STAGING_SSH_KEY_PATH" "${REMOTE_USER}@${STAGING_EC2_HOST}" \
  "umask 077; cat > '$REMOTE_DIR/.env'; chmod 600 '$REMOTE_DIR/.env'" \
  <"$STAGING_ENV_FILE"

scp -i "$STAGING_SSH_KEY_PATH" \
  "$STAGING_COMPOSE_FILE" \
  "${REMOTE_USER}@${STAGING_EC2_HOST}:${REMOTE_DIR}/docker-compose.yml"

scp -i "$STAGING_SSH_KEY_PATH" \
  infra/docker/postgres/configure-app-role.sql \
  "${REMOTE_USER}@${STAGING_EC2_HOST}:${REMOTE_DIR}/postgres/configure-app-role.sql"

# Private Docker Hub repository도 pull할 수 있도록 PAT은 SSH 표준입력으로만 전달합니다.
printf '%s' "$docker_registry_pat" |
  ssh -i "$STAGING_SSH_KEY_PATH" "${REMOTE_USER}@${STAGING_EC2_HOST}" \
    "docker login -u '$DOCKER_USER' --password-stdin"

unset docker_registry_pat

ssh -i "$STAGING_SSH_KEY_PATH" "${REMOTE_USER}@${STAGING_EC2_HOST}" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail

cd /home/ubuntu/finalproject-staging
set -a
source ./.env
set +a

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="deployment-evidence/$deployment_id"
umask 077
mkdir -p "$evidence_dir"

docker compose config --quiet
docker compose pull
docker compose up -d --wait postgres redis

# Schema 변경 중 구 애플리케이션이 DB를 사용하는 상황을 막습니다.
docker compose stop -t 60 nginx fastapi

docker compose exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  >"$evidence_dir/pre-migration.dump"

run_one_shot_service() {
  local service_name="$1"
  local container_name="$2"
  local exit_code

  docker compose up -d --force-recreate "$service_name"
  exit_code="$(docker wait "$container_name")"

  if [ "$exit_code" -ne 0 ]; then
    docker compose logs --no-color "$service_name" >"$evidence_dir/${service_name}-failure.log"
    printf '%s failed with exit code %s\n' "$service_name" "$exit_code" >&2
    return "$exit_code"
  fi

  docker compose logs --no-color "$service_name" >"$evidence_dir/${service_name}.log"
}

run_one_shot_service configure-db-roles finalproject-staging-configure-db-roles
run_one_shot_service migrate finalproject-staging-migrate

docker compose up -d --no-deps --wait fastapi
docker compose up -d --no-deps --wait nginx

docker compose exec -T nginx \
  wget -q -O - http://fastapi:8000/api/v1/health \
  >"$evidence_dir/health-response.json"

docker compose ps >"$evidence_dir/compose-ps.txt"
docker compose images >"$evidence_dir/compose-images.txt"

printf 'Staging deployment completed. Evidence: %s\n' "$evidence_dir"
docker compose ps
REMOTE_SCRIPT

printf 'Staging URL: %s\n' "$STAGING_PUBLIC_ORIGIN"
printf 'HTTP 배포에서는 Secure refresh cookie를 검증할 수 없습니다. 합성 데이터 smoke 후 HTTPS를 적용하세요.\n'
