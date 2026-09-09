#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/.."

PROD_ENV_FILE="${PROD_ENV_FILE:-envs/.prod.env}"

if [ ! -f "$PROD_ENV_FILE" ]; then
  echo "운영 환경파일을 찾을 수 없습니다: $PROD_ENV_FILE"
  exit 1
fi

set -a
source "$PROD_ENV_FILE"
set +a

if [ "${TLS_TERMINATION:-certbot}" != "certbot" ]; then
  echo "TLS_TERMINATION=${TLS_TERMINATION:-<empty>}에서는 Certbot을 실행하지 않습니다. CloudFront 기본 인증서를 사용하세요."
  exit 1
fi

for variable_name in PRODUCTION_DOMAIN CERTBOT_EMAIL; do
  if [ -z "${!variable_name:-}" ]; then
    echo "필수 인증서 환경변수가 비어 있습니다: $variable_name"
    exit 1
  fi
done

if grep -Eq '=(replace-with|replace_with)' "$PROD_ENV_FILE"; then
  echo "$PROD_ENV_FILE 안의 placeholder를 실제 운영 값으로 교체해야 합니다."
  exit 1
fi

if [[ ! "$PRODUCTION_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] ||
  [[ "$PRODUCTION_DOMAIN" != *.* ]]; then
  echo "PRODUCTION_DOMAIN은 유효한 hostname이어야 합니다."
  exit 1
fi

if [[ ! "$CERTBOT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "CERTBOT_EMAIL 형식이 올바르지 않습니다."
  exit 1
fi

for required_command in ssh scp; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "필수 명령을 찾을 수 없습니다: $required_command"
    exit 1
  fi
done

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

echo "${COLOR_BLUE}EC2 SSH key 파일명을 입력하세요(ex. ai_health_key.pem).${COLOR_NC}"
read -r -p "SSH 키 파일명: " ssh_key_file
echo ""

echo "${COLOR_BLUE}EC2 IP 또는 hostname을 입력하세요.${COLOR_NC}"
read -r -p "EC2 IP: " ec2_host
echo ""

SSH_KEY_PATH="$HOME/.ssh/$ssh_key_file"

if [ -z "$ssh_key_file" ] || [ ! -f "$SSH_KEY_PATH" ]; then
  echo "${COLOR_RED}SSH 키 파일을 찾을 수 없습니다: $SSH_KEY_PATH${COLOR_NC}"
  exit 1
fi

if [[ ! "$ec2_host" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "${COLOR_RED}EC2 IP 또는 hostname 형식이 올바르지 않습니다: $ec2_host${COLOR_NC}"
  exit 1
fi

chmod 400 "$SSH_KEY_PATH"

NGINX_TEMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$NGINX_TEMP_DIR"
}

trap cleanup EXIT

http_config_path="$NGINX_TEMP_DIR/http.conf"
https_config_path="$NGINX_TEMP_DIR/https.conf"

sed \
  "s/server_name .*/server_name ${PRODUCTION_DOMAIN};/g" \
  infra/nginx/prod_http.conf \
  >"$http_config_path"

sed \
  -e "s/server_name .*/server_name ${PRODUCTION_DOMAIN};/g" \
  -e "s|/etc/letsencrypt/live/[^/]*|/etc/letsencrypt/live/${PRODUCTION_DOMAIN}|g" \
  infra/nginx/prod_https.conf \
  >"$https_config_path"

printf -v remote_domain '%q' "$PRODUCTION_DOMAIN"
printf -v remote_email '%q' "$CERTBOT_EMAIL"

echo "${COLOR_BLUE}HTTP challenge 구성을 적용하고 인증서를 발급합니다.${COLOR_NC}"

scp \
  -i "$SSH_KEY_PATH" \
  "$http_config_path" \
  "ubuntu@$ec2_host":~/project/nginx/default.conf

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_host" \
  "CERT_DOMAIN=$remote_domain CERT_EMAIL=$remote_email bash -s" <<'EOF'
set -euo pipefail

cd "$HOME/project"

docker compose up -d --wait fastapi nginx

docker compose run --rm --entrypoint certbot certbot \
  certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --domain "$CERT_DOMAIN" \
  --agree-tos \
  --email "$CERT_EMAIL" \
  --non-interactive
EOF

echo "${COLOR_GREEN}인증서 발급이 완료되었습니다.${COLOR_NC}"
echo "${COLOR_BLUE}HTTPS 구성을 검증하고 적용합니다.${COLOR_NC}"

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_host" \
  'cp "$HOME/project/nginx/default.conf" "$HOME/project/nginx/default.conf.http-bootstrap"'

scp \
  -i "$SSH_KEY_PATH" \
  "$https_config_path" \
  "ubuntu@$ec2_host":~/project/nginx/default.conf

ssh \
  -i "$SSH_KEY_PATH" \
  "ubuntu@$ec2_host" <<'EOF'
set -euo pipefail

cd "$HOME/project"

if ! docker compose exec -T nginx nginx -t; then
  cp nginx/default.conf.http-bootstrap nginx/default.conf
  echo "HTTPS Nginx 설정 검증에 실패해 HTTP bootstrap 설정을 복구했습니다."
  exit 1
fi

docker compose exec -T nginx nginx -s reload
rm nginx/default.conf.http-bootstrap
docker compose up -d certbot
docker compose ps nginx certbot
EOF

echo "${COLOR_GREEN}HTTPS 적용이 완료되었습니다: https://${PRODUCTION_DOMAIN}${COLOR_NC}"
