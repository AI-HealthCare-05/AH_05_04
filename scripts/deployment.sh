#!/usr/bin/env bash

# 오류, 미정의 변수, pipeline 실패가 발생하면 즉시 중단합니다.
set -euo pipefail

# 어느 위치에서 실행해도 저장소 루트 기준으로 동작하도록 이동합니다.
cd "$(dirname "$0")/.."

PROD_ENV_FILE="envs/.prod.env"

if [ ! -f "$PROD_ENV_FILE" ]; then
  echo "운영 환경파일을 찾을 수 없습니다: $PROD_ENV_FILE"
  exit 1
fi

# 이미지 버전 등 운영 배포 설정을 읽습니다.
# 실제 secret이 포함된 .prod.env는 저장소에 커밋하지 않습니다.
set -a
source "$PROD_ENV_FILE"
set +a

# 터미널 색상을 지원하지 않는 환경에서는 빈 문자열을 사용합니다.
if [ -t 1 ] &&
  command -v tput >/dev/null 2>&1 &&
  tput colors >/dev/null 2>&1; then
  COLOR_GREEN="$(tput setaf 2)"
  COLOR_BLUE="$(tput setaf 4)"
  COLOR_RED="$(tput setaf 1)"
  COLOR_NC="$(tput sgr0)"
else
  COLOR_GREEN=""
  COLOR_BLUE=""
  COLOR_RED=""
  COLOR_NC=""
fi

# ---------- Docker 이미지 빌드 및 push ----------
build_and_push() {
  local docker_user="$1"
  local docker_repo="$2"
  local name="$3"
  local tag="$4"
  local dockerfile="$5"
  local context="$6"
  local tag_base

  if [[ "$name" == "FastAPI" ]]; then
    tag_base="app"
  else
    tag_base="ai"
  fi

  echo "${COLOR_BLUE}${name} Docker image build start.${COLOR_NC}"

  docker build \
    --platform linux/amd64 \
    -t "${docker_user}/${docker_repo}:${tag_base}-${tag}" \
    -f "$dockerfile" \
    "$context"

  echo "${COLOR_BLUE}${name} Docker image push start.${COLOR_NC}"

  docker push "${docker_user}/${docker_repo}:${tag_base}-${tag}"

  echo "${COLOR_GREEN}${name} done.${COLOR_NC}"
  echo ""
}

# ---------- Docker 로그인 ----------
echo "${COLOR_BLUE}Docker username과 PAT을 입력해주세요.${COLOR_NC}"

read -r -p "username: " docker_user
read -r -s -p "password: " docker_pw
echo ""
echo ""

if [ -z "$docker_user" ] || [ -z "$docker_pw" ]; then
  echo "${COLOR_RED}Docker username 또는 PAT이 입력되지 않았습니다.${COLOR_NC}"
  exit 1
fi

echo "${COLOR_BLUE}Docker login${COLOR_NC}"

# PAT이 command argument에 직접 노출되지 않도록 stdin으로 전달합니다.
if ! printf '%s' "$docker_pw" |
  docker login -u "$docker_user" --password-stdin; then
  echo "${COLOR_RED}Docker 로그인에 실패했습니다.${COLOR_NC}"
  exit 1
fi

echo "${COLOR_GREEN}Docker 로그인 성공!${COLOR_NC}"
echo ""

# ---------- Docker repository 입력 ----------
echo "${COLOR_BLUE}이미지를 업로드할 Docker repository 이름을 입력하세요.${COLOR_NC}"
read -r -p "Docker Repository Name: " docker_repo
echo ""

if [ -z "$docker_repo" ]; then
  echo "${COLOR_RED}Docker repository 이름이 입력되지 않았습니다.${COLOR_NC}"
  exit 1
fi

# ---------- 배포 이미지 선택 ----------
echo "${COLOR_BLUE}빌드하고 배포할 이미지를 선택하세요.${COLOR_NC}"
echo "1) fastapi"
echo "2) ai_worker"
read -r -p "선택 (복수 선택 가능, 예: 1 2): " selections
echo ""

if [ -z "$selections" ]; then
  echo "${COLOR_RED}배포 대상이 선택되지 않았습니다.${COLOR_NC}"
  exit 1
fi

DEPLOY_SERVICES=()

# ---------- 이미지 빌드 및 push ----------
for choice in $selections; do
  case "$choice" in
    1)
      echo "${COLOR_BLUE}FastAPI 배포 버전을 입력하세요(ex. v1.0.0).${COLOR_NC}"
      read -r -p "FastAPI 앱 버전: " fastapi_version

      if [ -z "$fastapi_version" ]; then
        echo "${COLOR_RED}FastAPI 버전이 입력되지 않았습니다.${COLOR_NC}"
        exit 1
      fi

      build_and_push \
        "$docker_user" \
        "$docker_repo" \
        "FastAPI" \
        "$fastapi_version" \
        "app/Dockerfile" \
        "."

      # 입력받은 버전을 원격 Compose image tag에 전달합니다.
      APP_VERSION="$fastapi_version"
      DEPLOY_SERVICES+=("fastapi")
      ;;
    2)
      echo "${COLOR_BLUE}AI Worker 배포 버전을 입력하세요(ex. v1.0.0).${COLOR_NC}"
      read -r -p "AI Worker 버전: " ai_version

      if [ -z "$ai_version" ]; then
        echo "${COLOR_RED}AI Worker 버전이 입력되지 않았습니다.${COLOR_NC}"
        exit 1
      fi

      build_and_push \
        "$docker_user" \
        "$docker_repo" \
        "AI Worker" \
        "$ai_version" \
        "ai_worker/Dockerfile" \
        "."

      # 입력받은 버전을 원격 Compose image tag에 전달합니다.
      AI_WORKER_VERSION="$ai_version"
      DEPLOY_SERVICES+=("ai-worker")
      ;;
    *)
      echo "${COLOR_RED}잘못된 선택입니다: $choice${COLOR_NC}"
      exit 1
      ;;
  esac
done

echo "${COLOR_GREEN}선택한 이미지의 build와 push가 완료되었습니다.${COLOR_NC}"
echo "${COLOR_BLUE}배포 대상 서비스: ${DEPLOY_SERVICES[*]}${COLOR_NC}"
echo ""

# ---------- SSH 접속 정보 입력 ----------
echo "${COLOR_BLUE}EC2 SSH key 파일명을 입력하세요(ex. ai_health_key.pem).${COLOR_NC}"
read -r -p "SSH 키 파일명: " ssh_key_file
echo ""

echo "${COLOR_BLUE}EC2 IP 또는 hostname을 입력하세요.${COLOR_NC}"
read -r -p "EC2 IP: " ec2_ip
echo ""

echo "${COLOR_BLUE}현재 서버의 HTTP/HTTPS 구성을 선택하세요.${COLOR_NC}"
echo "1) HTTP"
echo "2) HTTPS"
read -r -p "선택: " is_https
echo ""

SSH_KEY_PATH="$HOME/.ssh/$ssh_key_file"

if [ -z "$ssh_key_file" ]; then
  echo "${COLOR_RED}SSH 키 파일명이 입력되지 않았습니다.${COLOR_NC}"
  exit 1
fi

if [ ! -f "$SSH_KEY_PATH" ]; then
  echo "${COLOR_RED}SSH 키 파일을 찾을 수 없습니다: $SSH_KEY_PATH${COLOR_NC}"
  exit 1
fi

if [ -z "$ec2_ip" ]; then
  echo "${COLOR_RED}EC2 IP 또는 hostname이 입력되지 않았습니다.${COLOR_NC}"
  exit 1
fi

# SSH와 SCP를 실행하기 전에 key 파일 권한을 제한합니다.
chmod 400 "$SSH_KEY_PATH"

# ---------- 임시 Nginx 설정 준비 ----------
NGINX_TEMP_DIR="$(mktemp -d)"

cleanup() {
  # 저장소 원본 대신 임시 Nginx 설정만 제거합니다.
  rm -rf "$NGINX_TEMP_DIR"
}

trap cleanup EXIT

nginx_config_path="$NGINX_TEMP_DIR/default.conf"

case "$is_https" in
  1)
    # HTTP 환경에서는 EC2 주소를 server_name으로 사용합니다.
    sed \
      "s/server_name .*/server_name ${ec2_ip};/g" \
      infra/nginx/prod_http.conf \
      >"$nginx_config_path"
    ;;
  2)
    echo "${COLOR_BLUE}현재 사용 중인 도메인을 입력하세요.${COLOR_NC}"
    read -r -p "Domain: " domain

    if [ -z "$domain" ]; then
      echo "${COLOR_RED}도메인이 입력되지 않았습니다.${COLOR_NC}"
      exit 1
    fi

    # sed replacement에 안전한 기본 hostname 문자만 허용합니다.
    if [[ ! "$domain" =~ ^[A-Za-z0-9.-]+$ ]]; then
      echo "${COLOR_RED}도메인 형식이 올바르지 않습니다: $domain${COLOR_NC}"
      exit 1
    fi

    # HTTPS 환경에서는 server_name과 인증서 경로를 함께 설정합니다.
    sed \
      -e "s/server_name .*/server_name ${domain};/g" \
      -e "s|/etc/letsencrypt/live/[^/]*|/etc/letsencrypt/live/${domain}|g" \
      infra/nginx/prod_https.conf \
      >"$nginx_config_path"
    ;;
  *)
    echo "${COLOR_RED}HTTP/HTTPS 선택값이 올바르지 않습니다.${COLOR_NC}"
    exit 1
    ;;
esac

# ---------- EC2 배포 디렉터리 준비 ----------
echo "${COLOR_BLUE}EC2 배포 디렉터리를 준비합니다.${COLOR_NC}"

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  "mkdir -p ~/project/nginx ~/project/postgres"

# ---------- 운영 파일 복사 ----------
echo "${COLOR_BLUE}운영 환경파일과 Compose 설정을 복사합니다.${COLOR_NC}"

# 서버에서는 Compose와 같은 ~/project 디렉터리의 .env를 사용합니다.
scp \
  -i "$SSH_KEY_PATH" \
  "$PROD_ENV_FILE" \
  "ubuntu@$ec2_ip":~/project/.env

scp \
  -i "$SSH_KEY_PATH" \
  infra/docker/docker-compose.prod.yml \
  "ubuntu@$ec2_ip":~/project/docker-compose.yml

# 애플리케이션 제한 계정 구성 SQL을 서버로 복사합니다.
scp \
  -i "$SSH_KEY_PATH" \
  infra/docker/postgres/configure-app-role.sql \
  "ubuntu@$ec2_ip":~/project/postgres/configure-app-role.sql

# 임시로 생성한 Nginx 설정을 서버에 복사합니다.
scp \
  -i "$SSH_KEY_PATH" \
  "$nginx_config_path" \
  "ubuntu@$ec2_ip":~/project/nginx/default.conf

# ---------- 원격 명령에 전달할 값 안전하게 escape ----------
printf -v remote_docker_username '%q' "$docker_user"
printf -v remote_docker_pat '%q' "$docker_pw"
printf -v remote_docker_repository '%q' "$docker_repo"
printf -v remote_app_version '%q' "$APP_VERSION"
printf -v remote_ai_worker_version '%q' "$AI_WORKER_VERSION"
printf -v remote_deploy_services '%q' "${DEPLOY_SERVICES[*]}"

# ---------- EC2 배포 자동화 ----------
echo "${COLOR_BLUE}EC2 배포를 시작합니다.${COLOR_NC}"

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  "DOCKER_USERNAME=$remote_docker_username \
   DOCKER_PAT=$remote_docker_pat \
   DOCKER_USER=$remote_docker_username \
   DOCKER_REPOSITORY=$remote_docker_repository \
   APP_VERSION=$remote_app_version \
   AI_WORKER_VERSION=$remote_ai_worker_version \
   DEPLOY_SERVICES=$remote_deploy_services \
   bash -s" <<'EOF'
set -euo pipefail

cd "$HOME/project"

echo "Docker login"

# PAT을 stdin으로 전달하여 Docker login 명령 인수에 직접 넣지 않습니다.
printf '%s' "$DOCKER_PAT" |
  docker login -u "$DOCKER_USERNAME" --password-stdin

if [ -z "${DEPLOY_SERVICES// }" ]; then
  echo "배포 대상 서비스가 없습니다."
  exit 1
fi

read -r -a deploy_services <<<"$DEPLOY_SERVICES"

echo "Starting PostgreSQL and Redis"

# PostgreSQL과 Redis가 health check를 통과할 때까지 기다립니다.
docker compose up \
  -d \
  --pull always \
  --wait \
  postgres \
  redis

echo "Configuring restricted application database role"

# 신규·기존 DB에서 애플리케이션 제한 계정과 권한을 정렬합니다.
docker compose exec -T postgres \
  sh -lc '
    psql \
      -v ON_ERROR_STOP=1 \
      -U "$DB_MIGRATION_USER" \
      -d "$POSTGRES_DB" \
      -f /docker-entrypoint-initdb.d/configure-app-role.sql
  '

echo "Running Alembic migration"

# 종료된 migration 컨테이너를 재사용하지 않고 매 배포마다 새로 실행합니다.
docker compose up \
  -d \
  --pull always \
  --force-recreate \
  migrate

migration_exit_code="$(docker wait migrate)"

if [ "$migration_exit_code" -ne 0 ]; then
  echo "Alembic migration failed."
  docker compose logs --no-color migrate
  exit "$migration_exit_code"
fi

echo "Alembic migration completed successfully."
echo "Deploying services: ${deploy_services[*]}"

# --no-deps를 사용하지 않습니다.
# PostgreSQL health와 migration 성공 조건을 Compose가 다시 확인합니다.
docker compose up \
  -d \
  --pull always \
  "${deploy_services[@]}"

# 사용 중인 rollback image는 남기고 dangling image만 정리합니다.
docker image prune -f

docker compose ps
EOF

echo "${COLOR_GREEN}Deployment finished.${COLOR_NC}"
