#!/usr/bin/env bash

# 오류, 미정의 변수, pipeline 실패가 발생하면 즉시 중단합니다.
set -euo pipefail

# 어느 위치에서 실행해도 저장소 루트 기준으로 동작하도록 이동합니다.
cd "$(dirname "$0")/.."

PROD_ENV_FILE="${PROD_ENV_FILE:-envs/.prod.env}"

if [ ! -f "$PROD_ENV_FILE" ]; then
  echo "운영 환경파일을 찾을 수 없습니다: $PROD_ENV_FILE"
  exit 1
fi

# ---------- 필수 키 선언 검증 (source 이전) ----------
# source는 파일에 없는 변수를 초기화하지 않는다. 실행 셸에 REDIS_PASSWORD, ENV 또는
# snapshot 암호화 key가 이미 설정돼 있으면 파일에 값이 없어도 아래 검증을 통과할 수 있는데, 원격 배포는
# 이 파일 원문만 서버로 복사하므로(하단 scp 참고) 로컬 검증과 실제 전송 설정이
# 어긋나 필수 값이 없는 채로 배포될 수 있다(#321 리뷰). source 전에 파일 자체가
# 필수 값을 직접 선언하는지 먼저 확인한다.
for required_key in REDIS_PASSWORD ENV IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY ACCOUNT_WITHDRAWAL_REQUEST_ENABLED; do
  if ! grep -Eq "^${required_key}=" "$PROD_ENV_FILE"; then
    echo "$PROD_ENV_FILE에 $required_key가 선언되어 있지 않습니다."
    exit 1
  fi
done

# 이미지 버전 등 운영 배포 설정을 읽습니다.
# 실제 secret이 포함된 .prod.env는 저장소에 커밋하지 않습니다.
# 원격에는 .prod.env 원문만 복사되므로, 실행 셸에 상속된 cleanup 자격 증명이
# 파일의 선언 누락을 가리지 않도록 source 전에 비운다.
unset ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE
unset ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD
unset VITE_PUBLIC_TRACK_C
unset VITE_SIGNUP_TERMS_APPROVED
set -a
source "$PROD_ENV_FILE"
set +a

# ---------- Idempotency snapshot 암호화 key 검증 ----------
# 운영 Backend가 기동된 뒤 Config validation으로 발견하면 image build/push와 원격 compose
# 반영이 이미 진행된 뒤다. 필수 active key의 누락·공백·공개 placeholder를 외부 작업 전에
# 차단하고, 실제 값 자체는 출력하지 않는다. version과 retired key ring은 선택값이며
# Compose에서 Backend 기본값(v1, {})과 같은 fallback을 적용한다.
if [ -z "${IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY:-}" ]; then
  echo "필수 운영 환경변수가 비어 있습니다: IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY"
  exit 1
fi

case "$IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY" in
  MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= | replace-with* | replace_with*)
    echo "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY가 아직 placeholder 값입니다: $PROD_ENV_FILE 안의 값을 교체해야 합니다."
    exit 1
    ;;
esac

# CloudFront origin 검증 secret은 이후 실행되는 docker/ssh/scp 프로세스 환경에
# 불필요하게 상속하지 않습니다. Nginx 설정을 만들 때 현재 Bash 안에서만 씁니다.
export -n CLOUDFRONT_ORIGIN_VERIFY_SECRET 2>/dev/null || true
# ---------- PostgreSQL 역할 설정 검증 ----------
# 역할 이름이 같으면 Migration 역할의 NOSUPERUSER 설정이
# Bootstrap/admin 역할에도 적용될 수 있으므로 배포 전에 차단합니다.
required_db_variables=(
  DB_ADMIN_USER
  DB_ADMIN_PASSWORD
  DB_MIGRATION_USER
  DB_MIGRATION_PASSWORD
  DB_APP_USER
  DB_APP_PASSWORD
)

for variable_name in "${required_db_variables[@]}"; do
  if [ -z "${!variable_name:-}" ]; then
    echo "필수 운영 DB 환경변수가 비어 있습니다: $variable_name"
    exit 1
  fi
done

# ---------- 회원탈퇴 gate 검증 ----------
# Compose는 ACCOUNT_WITHDRAWAL_REQUEST_ENABLED를 ${...:-false}로 치환하므로, .prod.env에
# 선언 자체가 없으면 조용히 false가 주입되어 탈퇴 API가 503(fail-closed)으로 돌아간다.
# 선언 여부는 위 required_key 검사에서 막고, 여기서는 true로 열 때 필요한 cleanup 자격 증명을
# 확인한다. 원격에는 .prod.env 원문만 복사되므로(하단 ssh 참고) 파일의 직접 선언 여부를
# 셸 값과 함께 검사한다. Backend Config도 같은 조건을 validator로 막지만(#825), 그 실패는
# image push와 원격 compose 반영이 끝난 뒤 컨테이너 기동 시점에 드러나므로 먼저 차단한다.
case "$(printf '%s' "${ACCOUNT_WITHDRAWAL_REQUEST_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
  true | 1 | yes | on)
    for variable_name in ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD; do
      if ! grep -Eq "^${variable_name}=" "$PROD_ENV_FILE"; then
        echo "$PROD_ENV_FILE에 $variable_name이 선언되어 있지 않습니다."
        exit 1
      fi
      if [ -z "${!variable_name:-}" ]; then
        echo "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=true인데 $variable_name이 비어 있습니다."
        exit 1
      fi
    done
    ;;
esac

# ---------- Redis 인증 검증 ----------
# PUBLIC_TRACK_F_ENABLED/Track A Worker 모두 non-local(STAGING/PRODUCTION) 환경에서
# Redis 인증을 강제한다(#150). 값이 비어 있으면 compose가 빈 문자열로 치환해
# 무인증 Redis가 뜰 수 있으므로, docker login/build/push 같은 외부 작업 전에 차단한다.
if [ -z "${REDIS_PASSWORD:-}" ]; then
  echo "필수 운영 환경변수가 비어 있습니다: REDIS_PASSWORD"
  exit 1
fi

# ---------- Placeholder 값 검증 ----------
# example 파일을 그대로 복사해 배포하면 REDIS_PASSWORD 등 필수 값이 비어 있지 않아
# 위 -z 검사를 통과한다. 그 상태로 배포되면 git에 커밋된 공개 placeholder 값으로
# 운영 서비스가 인증을 걸고 뜬다(deploy-staging.sh와 동일한 검사).
if grep -Eq '=(replace-with|replace_with)' "$PROD_ENV_FILE"; then
  echo "$PROD_ENV_FILE 안의 placeholder를 실제 운영 값으로 교체해야 합니다."
  exit 1
fi

# 위 파일 원문 검사는 REDIS_PASSWORD="replace-with-..."처럼 따옴표로 감싼 값을
# 놓친다(#321 리뷰). source 이후 따옴표가 제거된 실제 셸 변수 값을 다시 검사한다.
case "$REDIS_PASSWORD" in
  replace-with* | replace_with*)
    echo "REDIS_PASSWORD가 아직 placeholder 값입니다: $PROD_ENV_FILE 안의 값을 교체해야 합니다."
    exit 1
    ;;
esac

# ---------- ENV 값 검증 ----------
# PROD_ENV_FILE 경로가 하드코딩이던 때는 문제가 아니었지만, override를 허용하면서
# PROD_ENV_FILE=envs/.local.env 같은 다른 환경파일로 운영 배포를 실행할 수 있게
# 됐다. deploy-staging.sh의 ENV 검사와 대칭으로 운영 배포는 ENV=production인
# 환경파일로만 실행되도록 강제한다.
if [ "${ENV:-}" != "production" ]; then
  echo "ENV는 production이어야 합니다. 현재 값: ${ENV:-<empty>}"
  exit 1
fi

if [ "$DB_ADMIN_USER" = "$DB_MIGRATION_USER" ] ||
  [ "$DB_ADMIN_USER" = "$DB_APP_USER" ] ||
  [ "$DB_MIGRATION_USER" = "$DB_APP_USER" ]; then
  echo "DB_ADMIN_USER, DB_MIGRATION_USER, DB_APP_USER는 서로 다른 이름이어야 합니다."
  exit 1
fi

# Writer is a separate login; never reuse a Runtime/Migration/Admin identity.
for variable_name in SOURCE_WRITER_USER SOURCE_WRITER_PASSWORD; do
  if [ -z "${!variable_name:-}" ]; then
    echo "필수 운영 DB 환경변수가 비어 있습니다: $variable_name"
    exit 1
  fi
  case "${!variable_name}" in
    replace-with* | replace_with*)
      echo "Writer 환경변수의 placeholder를 교체해야 합니다: $variable_name"
      exit 1
      ;;
  esac
done
if [ "$SOURCE_WRITER_USER" = "$DB_ADMIN_USER" ] ||
  [ "$SOURCE_WRITER_USER" = "$DB_MIGRATION_USER" ] ||
  [ "$SOURCE_WRITER_USER" = "$DB_APP_USER" ]; then
  echo "SOURCE_WRITER_USER는 Admin, Migration, Runtime과 다른 이름이어야 합니다."
  exit 1
fi

# ---------- 기간 한정 Production 데모 설정 검증 ----------
required_demo_variables=(
  DOCKER_USER
  DOCKER_REPOSITORY
  APP_VERSION
  FRONTEND_VERSION
  AI_WORKER_VERSION
  TLS_TERMINATION
  PRODUCTION_DOMAIN
  PRODUCTION_PUBLIC_ORIGIN
  COOKIE_DOMAIN
  CORS_ALLOWED_ORIGINS
)

for variable_name in "${required_demo_variables[@]}"; do
  if [ -z "${!variable_name:-}" ]; then
    echo "필수 운영 데모 환경변수가 비어 있습니다: $variable_name"
    exit 1
  fi
done

if [[ ! "$APP_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] ||
  [[ ! "$FRONTEND_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] ||
  [[ ! "$AI_WORKER_VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "이미지 version은 영문자, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다."
  exit 1
fi

if [ "$APP_VERSION" = "latest" ] || [ "$FRONTEND_VERSION" = "latest" ] ||
  [ "$AI_WORKER_VERSION" = "latest" ]; then
  echo "Rollback을 위해 latest 대신 commit SHA 또는 고정 version을 사용해야 합니다."
  exit 1
fi

if [[ ! "$DOCKER_USER" =~ ^[A-Za-z0-9._-]+$ ]] ||
  [[ ! "$DOCKER_REPOSITORY" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Docker registry 사용자와 repository 이름의 형식이 올바르지 않습니다."
  exit 1
fi

if [[ ! "$PRODUCTION_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] ||
  [[ "$PRODUCTION_DOMAIN" != *.* ]]; then
  echo "PRODUCTION_DOMAIN은 유효한 hostname이어야 합니다."
  exit 1
fi

expected_public_origin="https://${PRODUCTION_DOMAIN}"
if [ "$PRODUCTION_PUBLIC_ORIGIN" != "$expected_public_origin" ]; then
  echo "PRODUCTION_PUBLIC_ORIGIN은 $expected_public_origin 이어야 합니다."
  exit 1
fi

if [ "$COOKIE_DOMAIN" != "$PRODUCTION_DOMAIN" ] ||
  [ "$CORS_ALLOWED_ORIGINS" != "$PRODUCTION_PUBLIC_ORIGIN" ]; then
  echo "COOKIE_DOMAIN과 CORS_ALLOWED_ORIGINS는 Production 동일 origin과 일치해야 합니다."
  exit 1
fi

case "$TLS_TERMINATION" in
  cloudfront)
    if [[ ! "$PRODUCTION_DOMAIN" =~ ^[A-Za-z0-9-]+\.cloudfront\.net$ ]]; then
      echo "CloudFront 모드의 PRODUCTION_DOMAIN은 AWS가 발급한 *.cloudfront.net hostname이어야 합니다."
      exit 1
    fi

    if [[ ! "${CLOUDFRONT_ORIGIN_VERIFY_SECRET:-}" =~ ^[A-Za-z0-9_-]{32,128}$ ]]; then
      echo "CLOUDFRONT_ORIGIN_VERIFY_SECRET은 32~128자의 영문자, 숫자, 밑줄, 하이픈이어야 합니다."
      exit 1
    fi
    ;;
  certbot)
    if [[ ! "${CERTBOT_EMAIL:-}" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
      echo "Certbot 모드에서는 올바른 CERTBOT_EMAIL이 필요합니다."
      exit 1
    fi
    ;;
  *)
    echo "TLS_TERMINATION은 cloudfront 또는 certbot이어야 합니다."
    exit 1
    ;;
esac

# Worker Provider 설정 오류는 이미지 push와 원격 변경 전에 차단합니다.
for variable_name in CLOVA_OCR_INVOKE_URL CLOVA_OCR_SECRET; do
  if ! grep -Eq "^${variable_name}=" "$PROD_ENV_FILE"; then
    echo "$PROD_ENV_FILE에 ${variable_name}가 선언되어 있지 않습니다."
    exit 1
  fi
  if [ -z "${!variable_name:-}" ]; then
    echo "필수 Worker 환경변수가 비어 있습니다: $variable_name"
    exit 1
  fi
  case "${!variable_name}" in
    *replace-with* | *replace_with*)
      echo "Worker 환경변수의 placeholder를 교체해야 합니다: $variable_name"
      exit 1
      ;;
  esac
done
if [[ ! "$CLOVA_OCR_INVOKE_URL" =~ ^https://[^[:space:]]+$ ]]; then
  echo "CLOVA_OCR_INVOKE_URL은 HTTPS endpoint이어야 합니다."
  exit 1
fi

# Chat History 승인과 별개로 Track F·OCR·Protected Retrieval 공개 범위는 확장하지 않습니다.
for variable_name in PUBLIC_TRACK_F_ENABLED OCR_STRUCTURE_LLM_ENABLED PROTECTED_RETRIEVAL_ENABLED; do
  case "${!variable_name:-false}" in
    false | False | FALSE | 0) ;;
    *)
      echo "합성 OCR 데모에서는 $variable_name=false가 필요합니다."
      exit 1
      ;;
  esac
done

# 약관 승인은 환경파일에 직접 선언해야 하며 실행 셸의 값을 상속하지 않습니다.
validate_signup_terms_configuration() {
  if ! grep -Eq '^VITE_SIGNUP_TERMS_APPROVED=' "$PROD_ENV_FILE"; then
    echo "${PROD_ENV_FILE}에 VITE_SIGNUP_TERMS_APPROVED를 명시해야 합니다."
    return 1
  fi
  case "${VITE_SIGNUP_TERMS_APPROVED:-}" in
    true | false) ;;
    *)
      echo "VITE_SIGNUP_TERMS_APPROVED는 true 또는 false여야 합니다."
      return 1
      ;;
  esac
}
validate_signup_terms_configuration

# Track C 공개 게이트도 환경파일에 직접 선언해야 하며 실행 셸의 값을 상속하지 않습니다.
# 승인 조건은 docs/release-gates/post-mvp-1-external-approvals.md를 따릅니다.
validate_public_track_c_configuration() {
  if ! grep -Eq '^VITE_PUBLIC_TRACK_C=' "$PROD_ENV_FILE"; then
    echo "${PROD_ENV_FILE}에 VITE_PUBLIC_TRACK_C를 명시해야 합니다."
    return 1
  fi
  case "${VITE_PUBLIC_TRACK_C:-}" in
    true | false) ;;
    *)
      echo "VITE_PUBLIC_TRACK_C는 true 또는 false여야 합니다."
      return 1
      ;;
  esac
}
validate_public_track_c_configuration

for required_command in docker ssh scp; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "필수 명령을 찾을 수 없습니다: $required_command"
    exit 1
  fi
done

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
  shift 6
  local build_args=(--platform linux/amd64)
  local build_arg
  for build_arg in "$@"; do
    build_args+=(--build-arg "$build_arg")
  done
  local tag_base

  case "$name" in
    FastAPI) tag_base="app" ;;
    "AI Worker") tag_base="ai" ;;
    Frontend) tag_base="frontend" ;;
    *)
      echo "지원하지 않는 image 종류입니다: $name"
      return 1
      ;;
  esac

  echo "${COLOR_BLUE}${name} Docker image build start.${COLOR_NC}"

  docker build \
    "${build_args[@]}" \
    -t "${docker_user}/${docker_repo}:${tag_base}-${tag}" \
    -f "$dockerfile" \
    "$context"

  echo "${COLOR_BLUE}${name} Docker image push start.${COLOR_NC}"

  docker push "${docker_user}/${docker_repo}:${tag_base}-${tag}"

  echo "${COLOR_GREEN}${name} done.${COLOR_NC}"
  echo ""
}

# ---------- Docker 로그인 ----------
docker_user="$DOCKER_USER"
docker_repo="$DOCKER_REPOSITORY"

echo "${COLOR_BLUE}${docker_user} 계정의 Docker registry PAT을 입력해주세요.${COLOR_NC}"
read -r -s -p "password: " docker_pw
echo ""
echo ""

if [ -z "$docker_pw" ]; then
  echo "${COLOR_RED}Docker registry PAT이 입력되지 않았습니다.${COLOR_NC}"
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

# ---------- 데모 배포 image build 및 push ----------
# API·Worker·Frontend를 같은 배포 단위로 빌드합니다.
build_and_push \
  "$docker_user" \
  "$docker_repo" \
  "FastAPI" \
  "$APP_VERSION" \
  "backend/app/Dockerfile" \
  "."

build_and_push \
  "$docker_user" \
  "$docker_repo" \
  "Frontend" \
  "$FRONTEND_VERSION" \
  "frontend/Dockerfile.prod" \
  "." \
  "VITE_API_BASE_URL=$PRODUCTION_PUBLIC_ORIGIN" \
  "VITE_SIGNUP_TERMS_APPROVED=$VITE_SIGNUP_TERMS_APPROVED" \
  "VITE_EMAIL_VERIFICATION_ENABLED=${VITE_EMAIL_VERIFICATION_ENABLED:-false}" \
  "VITE_PUBLIC_TRACK_C=$VITE_PUBLIC_TRACK_C"

build_and_push \
  "$docker_user" \
  "$docker_repo" \
  "AI Worker" \
  "$AI_WORKER_VERSION" \
  "ai_worker/Dockerfile" \
  "."

DEPLOY_SERVICES=("fastapi" "ai-worker" "nginx" "checkin-deadline-scheduler")

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

if [ "$TLS_TERMINATION" = "cloudfront" ]; then
  is_https="cloudfront"
  echo "${COLOR_BLUE}CloudFront가 viewer HTTPS를 종료하므로 EC2에는 HTTP origin 구성을 적용합니다.${COLOR_NC}"
  echo ""
else
  echo "${COLOR_BLUE}현재 서버의 HTTP/HTTPS 구성을 선택하세요.${COLOR_NC}"
  echo "1) HTTP"
  echo "2) HTTPS"
  read -r -p "선택: " is_https
  echo ""
fi

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

if [[ ! "$ec2_ip" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "${COLOR_RED}EC2 IP 또는 hostname 형식이 올바르지 않습니다: $ec2_ip${COLOR_NC}"
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
  cloudfront)
    # Secret을 sed 인자로 넘기면 짧은 시간이라도 프로세스 목록에 노출될 수 있다.
    # Bash 문자열 치환과 builtin printf만 사용해 임시 설정을 렌더링합니다.
    nginx_config="$(<infra/nginx/prod_cloudfront.conf)"
    nginx_config="${nginx_config//production.cloudfront.net/$PRODUCTION_DOMAIN}"
    nginx_config="${nginx_config//__CLOUDFRONT_ORIGIN_VERIFY_SECRET__/$CLOUDFRONT_ORIGIN_VERIFY_SECRET}"
    printf '%s\n' "$nginx_config" >"$nginx_config_path"
    unset nginx_config
    ;;
  1)
    # 최초 인증서 발급을 위한 HTTP bootstrap도 운영 도메인을 사용합니다.
    sed \
      "s/server_name .*/server_name ${PRODUCTION_DOMAIN};/g" \
      infra/nginx/prod_http.conf \
      >"$nginx_config_path"
    ;;
  2)
    # HTTPS 환경에서는 server_name과 인증서 경로를 함께 설정합니다.
    sed \
      -e "s/server_name .*/server_name ${PRODUCTION_DOMAIN};/g" \
      -e "s|/etc/letsencrypt/live/[^/]*|/etc/letsencrypt/live/${PRODUCTION_DOMAIN}|g" \
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

# 운영 환경파일이 저장되는 project 디렉터리는 소유자만 접근할 수 있게 합니다.
ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  'install -d -m 700 \
    "$HOME/project" \
    "$HOME/project/nginx" \
    "$HOME/project/postgres"'

# ---------- 운영 파일 복사 ----------
echo "${COLOR_BLUE}운영 환경파일과 Compose 설정을 복사합니다.${COLOR_NC}"

# 환경파일 내용을 SSH 표준입력으로 전달합니다.
# 원격 파일은 생성 시점부터 소유자만 읽고 쓸 수 있게 제한합니다.
ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  'umask 077
   cat > "$HOME/project/.env"
   chmod 600 "$HOME/project/.env"' \
  <"$PROD_ENV_FILE"

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

# CloudFront origin 검증 secret이 포함될 수 있으므로 원격 Nginx 설정도 제한합니다.
ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  'chmod 600 "$HOME/project/nginx/default.conf"'

# ---------- 원격 명령에 전달할 값 안전하게 escape ----------
printf -v remote_docker_username '%q' "$docker_user"
printf -v remote_docker_repository '%q' "$docker_repo"
printf -v remote_app_version '%q' "$APP_VERSION"
printf -v remote_frontend_version '%q' "$FRONTEND_VERSION"
printf -v remote_ai_worker_version '%q' "$AI_WORKER_VERSION"
printf -v remote_deploy_services '%q' "${DEPLOY_SERVICES[*]}"

# PAT은 SSH 명령 인자나 환경변수에 포함하지 않고 표준입력으로만 전달합니다.
echo "${COLOR_BLUE}Docker registry에 로그인합니다.${COLOR_NC}"

printf '%s' "$docker_pw" |
  ssh \
    -i "$SSH_KEY_PATH" \
    "ubuntu@$ec2_ip" \
    "docker login \
      -u $remote_docker_username \
      --password-stdin"

# ---------- EC2 배포 자동화 ----------
echo "${COLOR_BLUE}EC2 배포를 시작합니다.${COLOR_NC}"

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_ip" \
  "DOCKER_USER=$remote_docker_username \
   DOCKER_REPOSITORY=$remote_docker_repository \
   APP_VERSION=$remote_app_version \
   FRONTEND_VERSION=$remote_frontend_version \
   AI_WORKER_VERSION=$remote_ai_worker_version \
   DEPLOY_SERVICES=$remote_deploy_services \
   bash -s" <<'EOF'
set -euo pipefail

# 함수 전체를 먼저 읽고 실행하여 하위 명령이 배포 스크립트 stdin을 소비하지 않게 합니다.
run_remote_deployment() {
cd "$HOME/project"

if [ -z "${DEPLOY_SERVICES// }" ]; then
  echo "배포 대상 서비스가 없습니다."
  exit 1
fi

read -r -a deploy_services <<<"$DEPLOY_SERVICES"
deployment_id="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="deployment-evidence/$deployment_id"
umask 077
mkdir -p "$evidence_dir"

write_deployment_db_snapshot() {
  local output_path="$1"

  # SQL은 별도 stdin으로 전달해 원격 Bash와 SQL 문자열의 중첩 quoting을 피합니다.
  docker compose exec -T postgres \
    sh -c 'exec psql -v ON_ERROR_STOP=1 -q -At -F "$(printf "\t")" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    >"$output_path" <<'SQL'
CREATE TEMP TABLE deployment_snapshot(name text, value text);
DO $$
DECLARE
  table_name text;
BEGIN
  IF to_regclass('public.alembic_version') IS NULL THEN
    INSERT INTO deployment_snapshot VALUES ('alembic_revision', NULL);
  ELSE
    EXECUTE 'INSERT INTO deployment_snapshot SELECT ''alembic_revision'', version_num FROM alembic_version ORDER BY version_num LIMIT 1';
  END IF;
  FOREACH table_name IN ARRAY ARRAY['user', 'profile', 'medical_document', 'prescription', 'guide', 'chat_session'] LOOP
    IF to_regclass(format('public.%I', table_name)) IS NULL THEN
      INSERT INTO deployment_snapshot VALUES (table_name, NULL);
    ELSE
      EXECUTE format('INSERT INTO deployment_snapshot SELECT %L, count(*)::text FROM public.%I', table_name, table_name);
    END IF;
  END LOOP;
END $$;
SELECT name, value FROM deployment_snapshot ORDER BY name;
DROP TABLE deployment_snapshot;
SQL
}

echo "Starting PostgreSQL and Redis"

# PostgreSQL과 Redis가 health check를 통과할 때까지 기다립니다.
docker compose up \
  -d \
  --pull always \
  --wait \
  postgres \
  redis

# #434: notification-scheduler는 notifications profile의 opt-in 서비스다.
# 일반 배포가 이를 자동 활성화해서도 안 되고, migration 때문에 멈춘 뒤 그대로
# 방치해서도 안 되므로, 정지 전에 현재 기동 상태를 기록해 배포 성공 후 복원한다.
if ! services_running_before_deploy="$(docker compose ps --services --status running)"; then
  echo "Could not confirm service running state before deployment."
  exit 1
fi

notification_scheduler_was_running=false
if printf '%s\n' "$services_running_before_deploy" | grep -qx 'notification-scheduler'; then
  notification_scheduler_was_running=true
fi

echo "notification-scheduler running before deployment: $notification_scheduler_was_running"

echo "Stopping application services before schema migration"

# Schema migration 전에 기존 애플리케이션을 먼저 멈춰 구버전 코드가 변경 중인
# DB schema를 읽거나 쓰는 상황을 방지합니다.
docker compose --profile notifications stop -t 15 notification-scheduler
docker compose stop -t 15 checkin-deadline-scheduler

docker compose --profile source-admin --profile catalog-admin --profile candidate-index-admin stop \
  -t 90 \
  fastapi \
  ai-worker \
  source-writer \
  catalog-writer \
  candidate-index-builder

if ! running_application_services="$(docker compose ps --services --status running)"; then
  echo "Could not confirm application service stop state."
  docker compose ps fastapi ai-worker || true
  exit 1
fi

if printf '%s\n' "$running_application_services" | grep -Eq '^(fastapi|ai-worker|source-writer|catalog-writer|candidate-index-builder|notification-scheduler|checkin-deadline-scheduler)$'; then
  echo "Application services are still running after stop request."
  docker compose ps fastapi ai-worker
  exit 1
fi

echo "Configuring restricted database roles"

# 역할 생성과 권한 설정은 Bootstrap/admin 계정으로만 실행합니다.
# psql 명령의 line continuation 사이에는 주석을 넣지 않습니다.
docker compose exec -T postgres \
  sh -lc '
    psql \
      -v ON_ERROR_STOP=1 \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -f /docker-entrypoint-initdb.d/configure-app-role.sql
  '

echo "Creating pre-migration backup and schema snapshot"

docker compose exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  >"$evidence_dir/pre-migration.dump"

write_deployment_db_snapshot "$evidence_dir/pre-migration-snapshot.tsv"

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
echo "Verifying final database head and catalog state"
docker compose --profile database-maintenance run --rm --no-deps --pull always verify-db-head

echo "Applying explicit Runtime and Source Writer permissions"
docker compose --profile database-admin run --rm --no-deps --pull always provision-db-roles

write_deployment_db_snapshot "$evidence_dir/post-migration-snapshot.tsv"
echo "Validating profile migration integrity"

profile_validation_output="$(
  docker compose exec -T postgres \
    sh -lc '
      psql \
        -v ON_ERROR_STOP=1 \
        -At \
        -F "$(printf "\t")" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" <<'"'"'SQL'"'"'
SELECT '"'"'user'"'"', count(*) FROM "user";
SELECT '"'"'self_profile'"'"', count(*) FROM profile WHERE profile_type = '"'"'SELF'"'"';
SELECT '"'"'medical_document_profile_null'"'"', count(*) FROM medical_document WHERE profile_id IS NULL;
SELECT '"'"'prescription_profile_null'"'"', count(*) FROM prescription WHERE profile_id IS NULL;
SELECT '"'"'guide_profile_null'"'"', count(*) FROM guide WHERE profile_id IS NULL;
SELECT '"'"'chat_session_profile_null'"'"', count(*) FROM chat_session WHERE profile_id IS NULL;
SELECT '"'"'prescription_profile_mismatch'"'"', count(*)
FROM prescription
JOIN medical_document ON medical_document.id = prescription.document_id
WHERE prescription.profile_id <> medical_document.profile_id;
SELECT '"'"'guide_profile_mismatch'"'"', count(*)
FROM guide
JOIN prescription ON prescription.id = guide.prescription_id
WHERE guide.profile_id <> prescription.profile_id;
SELECT '"'"'chat_session_profile_mismatch'"'"', count(*)
FROM chat_session
JOIN prescription ON prescription.id = chat_session.prescription_id
WHERE chat_session.profile_id <> prescription.profile_id;
SQL
    '
)"

printf '%s\n' "$profile_validation_output" >"$evidence_dir/post-migration-profile-validation.tsv"

if ! printf '%s\n' "$profile_validation_output" |
  awk -F '\t' '
    BEGIN {
      split("user self_profile medical_document_profile_null prescription_profile_null guide_profile_null chat_session_profile_null prescription_profile_mismatch guide_profile_mismatch chat_session_profile_mismatch", names, " ")
      for (i in names) expected[names[i]] = 1
    }
    NF != 2 || !($1 in expected) || $2 !~ /^[0-9]+$/ { failed = 1; next }
    {
      if (++seen[$1] != 1) failed = 1
      counts[$1] = $2
      if ($1 != "user" && $1 != "self_profile" && $2 != 0) failed = 1
    }
    END {
      for (name in expected) if (seen[name] != 1) failed = 1
      if (counts["user"] != counts["self_profile"]) failed = 1
      exit failed
    }
  '; then
  echo "Profile migration validation failed: incomplete/invalid counts, SELF count difference, or null/mismatch rows."
  exit 1
fi

# Worker mount는 read-only이므로 API image로 공유 업로드 경로를 먼저 생성합니다.
docker compose run --rm --no-deps --pull always --entrypoint python fastapi \
  -c 'import os; os.makedirs(os.environ["STORAGE_DIR"], exist_ok=True)'

echo "Deploying services: ${deploy_services[*]}"

# --no-deps를 사용하지 않습니다.
# PostgreSQL health와 migration 성공 조건을 Compose가 다시 확인합니다.
docker compose up \
  -d \
  --pull always \
  --wait \
  "${deploy_services[@]}"

# #839: 기한 처리 런타임이 멈춘 채 배포가 끝나면 사용자에게 보이는 Check-in 상태가
# 계속 어긋나므로, 배포한 경우 기동 여부를 확인하고 실패하면 배포를 중단합니다.
# 배포 대상에 없으면 확인할 대상도 없으므로 건너뜁니다. DEPLOY_SERVICES 포함 여부는
# tests/contract/test_checkin_deadline_runtime_configuration.py가 고정합니다.
checkin_deadline_scheduler_deployed=false
for deploy_service in "${deploy_services[@]}"; do
  if [ "$deploy_service" = "checkin-deadline-scheduler" ]; then
    checkin_deadline_scheduler_deployed=true
  fi
done

if [ "$checkin_deadline_scheduler_deployed" = true ]; then
  if ! running_services_after_deploy="$(docker compose ps --services --status running)"; then
    echo "Could not confirm service running state after deployment."
    docker compose ps -a checkin-deadline-scheduler || true
    exit 1
  fi

  if ! printf '%s\n' "$running_services_after_deploy" | grep -qx 'checkin-deadline-scheduler'; then
    echo "checkin-deadline-scheduler is not running after deployment."
    docker compose ps -a checkin-deadline-scheduler
    exit 1
  fi
fi

# #434: 배포 전에 running이던 경우에만 원래 상태로 되돌린다. 정지 상태였다면 그대로 둔다.
# "일반 배포가 알림을 자동 활성화하지 않는다"는 운영 계약(docs/deployment.md)을 지키면서,
# migration 때문에 멈춘 서비스가 방치되는 경우만 막는다. Production 최초 활성화는
# 여전히 #230 승인 후 운영자가 별도로 수행한다.
if [ "$notification_scheduler_was_running" = true ]; then
  echo "Restoring notification-scheduler to its pre-deployment running state"

  # 이 시점에는 migration·DB head·역할 권한 검증이 모두 끝났다. 단순 상태 복구이므로
  # 운영 Runbook의 수동 재생성과 동일하게 --no-deps로 의존성 해석을 다시 열지 않는다.
  docker compose --profile notifications up \
    -d \
    --no-deps \
    --pull always \
    --wait \
    notification-scheduler

  if ! running_services_after_restore="$(docker compose ps --services --status running)"; then
    echo "Could not confirm notification-scheduler running state after deployment."
    docker compose ps -a notification-scheduler || true
    exit 1
  fi

  if ! printf '%s\n' "$running_services_after_restore" | grep -qx 'notification-scheduler'; then
    echo "notification-scheduler was running before deployment but is stopped now."
    docker compose ps -a notification-scheduler
    exit 1
  fi
else
  echo "notification-scheduler stays stopped; it was not running before deployment."
fi

# 사용 중인 rollback image는 남기고 dangling image만 정리합니다.
docker image prune -f

docker compose ps
}

# 명시적인 SQL heredoc은 각 명령의 stdin으로 계속 전달됩니다.
run_remote_deployment </dev/null
EOF

echo "${COLOR_GREEN}Deployment finished.${COLOR_NC}"

if [ "$is_https" = "cloudfront" ]; then
  echo "${COLOR_BLUE}Smoke test: ${PRODUCTION_PUBLIC_ORIGIN}/healthz 및 ${PRODUCTION_PUBLIC_ORIGIN}/api/openapi.json${COLOR_NC}"
  echo "${COLOR_BLUE}EC2 80번 inbound가 CloudFront origin-facing prefix list로만 제한됐는지 확인하세요.${COLOR_NC}"
elif [ "$is_https" = "1" ]; then
  echo "${COLOR_BLUE}다음 단계: DNS가 ${PRODUCTION_DOMAIN}을 가리키는지 확인한 뒤 scripts/certbot.sh를 실행하세요.${COLOR_NC}"
else
  echo "${COLOR_BLUE}Smoke test: ${PRODUCTION_PUBLIC_ORIGIN}/healthz 및 ${PRODUCTION_PUBLIC_ORIGIN}/api/openapi.json${COLOR_NC}"
fi
