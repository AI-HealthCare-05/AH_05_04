from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE_PATH = PROJECT_ROOT / "infra/docker/docker-compose.prod.yml"
NGINX_DIR = PROJECT_ROOT / "infra/nginx"
PRODUCTION_RUNBOOK_PATH = PROJECT_ROOT / "docs/runbooks/aws-production-demo.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_compose_serves_immutable_frontend_image_behind_healthy_api() -> None:
    compose = yaml.safe_load(_read(PRODUCTION_COMPOSE_PATH))
    services = compose["services"]
    nginx = services["nginx"]
    fastapi = services["fastapi"]

    assert nginx["image"] == "${DOCKER_USER}/${DOCKER_REPOSITORY}:frontend-${FRONTEND_VERSION}"
    assert nginx["ports"] == ["80:80", "443:443"]
    assert "./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" in nginx["volumes"]
    assert "certbot-conf:/etc/letsencrypt:ro" in nginx["volumes"]
    assert nginx["depends_on"]["fastapi"]["condition"] == "service_healthy"
    assert nginx["healthcheck"]
    assert fastapi["healthcheck"]


def test_frontend_production_image_requires_api_origin_and_contains_built_spa() -> None:
    dockerfile = _read(PROJECT_ROOT / "frontend/Dockerfile.prod")

    assert "ARG VITE_API_BASE_URL" in dockerfile
    assert 'test -n "$VITE_API_BASE_URL"' in dockerfile
    assert 'VITE_API_BASE_URL="$VITE_API_BASE_URL" pnpm build' in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert "HEALTHCHECK" in dockerfile


@pytest.mark.parametrize(
    "config_name",
    ["prod_http.conf", "prod_https.conf", "prod_cloudfront.conf"],
)
def test_production_nginx_supports_spa_api_health_and_ocr_timeout(config_name: str) -> None:
    config = _read(NGINX_DIR / config_name)

    assert "location = /healthz" in config
    assert "location /api/" in config
    assert "proxy_pass http://fastapi;" in config
    assert "proxy_read_timeout 75s;" in config
    assert "proxy_send_timeout 75s;" in config
    assert "location /assets/" in config
    assert "try_files $uri $uri/ /index.html;" in config


def test_https_nginx_redirects_http_and_uses_managed_certificate_volume() -> None:
    config = _read(NGINX_DIR / "prod_https.conf")

    assert "return 301 https://$host$request_uri;" in config
    assert "listen 443 ssl;" in config
    assert "/etc/letsencrypt/live/production.example.com/fullchain.pem" in config
    assert "/etc/letsencrypt/live/production.example.com/privkey.pem" in config


def test_cloudfront_nginx_requires_origin_secret_and_preserves_viewer_https() -> None:
    config = _read(NGINX_DIR / "prod_cloudfront.conf")

    assert "__CLOUDFRONT_ORIGIN_VERIFY_SECRET__" in config
    assert "map $http_x_origin_verify $cloudfront_origin_verified" in config
    assert config.count("if ($cloudfront_origin_verified = 0)") == 3
    assert config.count("return 403;") == 3
    assert "proxy_set_header X-Forwarded-Proto https;" in config
    assert "listen 443" not in config


def test_production_scripts_use_env_domain_and_do_not_mutate_source_nginx_configs() -> None:
    deployment_script = _read(PROJECT_ROOT / "scripts/deployment.sh")
    certbot_script = _read(PROJECT_ROOT / "scripts/certbot.sh")

    assert 'expected_public_origin="https://${PRODUCTION_DOMAIN}"' in deployment_script
    assert 'if [ "$TLS_TERMINATION" = "cloudfront" ]' in deployment_script
    assert "prod_cloudfront.conf" in deployment_script
    assert "CLOUDFRONT_ORIGIN_VERIFY_SECRET" in deployment_script
    assert "*.cloudfront.net hostname" in deployment_script
    assert "export -n CLOUDFRONT_ORIGIN_VERIFY_SECRET" in deployment_script
    assert 'chmod 600 "$HOME/project/nginx/default.conf"' in deployment_script
    unsafe_secret_sed = "s/__CLOUDFRONT_ORIGIN_VERIFY_SECRET__/${CLOUDFRONT_ORIGIN_VERIFY_SECRET}"
    assert unsafe_secret_sed not in deployment_script
    assert '"VITE_API_BASE_URL=$PRODUCTION_PUBLIC_ORIGIN"' in deployment_script
    assert 'DEPLOY_SERVICES=("fastapi" "nginx")' in deployment_script
    assert 'if [ "$APP_VERSION" = "latest" ]' in deployment_script
    assert "--wait" in deployment_script
    assert (
        "AI Worker"
        not in deployment_script.split("# ---------- 데모 배포 image build 및 push ----------", 1)[1].split(
            "DEPLOY_SERVICES=", 1
        )[0]
    )

    assert 'source "$PROD_ENV_FILE"' in certbot_script
    assert '"$http_config_path"' in certbot_script
    assert '"$https_config_path"' in certbot_script
    assert "sed -i" not in certbot_script
    assert "sudo wget" not in certbot_script
    assert "nginx -t" in certbot_script
    assert "HTTP bootstrap 설정을 복구했습니다" in certbot_script
    assert 'if [ "${TLS_TERMINATION:-certbot}" != "certbot" ]' in certbot_script


def test_production_example_declares_demo_origin_and_frontend_version() -> None:
    env_example = _read(PROJECT_ROOT / "envs/example.prod.env")

    for key in (
        "FRONTEND_VERSION",
        "TLS_TERMINATION",
        "PRODUCTION_DOMAIN",
        "PRODUCTION_PUBLIC_ORIGIN",
        "CLOUDFRONT_ORIGIN_VERIFY_SECRET",
        "CERTBOT_EMAIL",
    ):
        assert f"{key}=" in env_example

    assert "TLS_TERMINATION=cloudfront" in env_example
    assert "PRODUCTION_DOMAIN=replace-with-distribution-id.cloudfront.net" in env_example
    assert "COOKIE_DOMAIN=replace-with-distribution-id.cloudfront.net" in env_example
    assert "CORS_ALLOWED_ORIGINS=https://replace-with-distribution-id.cloudfront.net" in env_example
    assert "CERTBOT_EMAIL=\n" in env_example


def test_production_runbook_covers_frontend_rediscovery_smoke_and_safe_evidence() -> None:
    runbook = _read(PRODUCTION_RUNBOOK_PATH)

    for expected_step in (
        "OCR 결과를 검수·확정",
        "복약 가이드로 진입",
        "복약 챗봇 도지와 이야기하기",
        "같은 합성 계정으로 다시 로그인",
        "기존 최신 Prescription과 연결된 Guide",
        "동일 세션의 USER 질문",
    ):
        assert expected_step in runbook

    for rediscovery_request in (
        "GET /api/v1/prescriptions/latest",
        "GET /api/v1/prescriptions/<redacted>/guide",
        "GET /api/v1/prescriptions/<redacted>/chat-session",
        "GET /api/v1/chat-sessions/<redacted>/messages",
    ):
        assert rediscovery_request in runbook

    assert "Guide 또는 Chat을 새로 만드는 `POST`가 발생하면 통과로 기록하지 않습니다" in runbook
    assert "Authorization/Cookie header" in runbook
    assert "Runbook에 절차가 있다는 사실만으로 smoke를 통과한 것으로 간주하지 않습니다" in runbook


def test_production_runbook_covers_cloudfront_default_domain_and_nine_day_teardown() -> None:
    runbook = _read(PRODUCTION_RUNBOOK_PATH)

    for expected_setting in (
        "2026-09-22부터 2026-09-30까지 9일",
        "*.cloudfront.net",
        "TLS_TERMINATION=cloudfront",
        "Redirect HTTP to HTTPS",
        "CachingDisabled",
        "AllViewerExceptHostHeader",
        "X-Origin-Verify",
        "origin-facing",
        "Origin response timeout을 `75초`",
        "CloudFront 모드에서는 `scripts/certbot.sh`를 실행하지 않습니다",
        "## 8. 2026-09-30 철거",
    ):
        assert expected_setting in runbook

    assert "기술 배포 승인과 기술 Rollback 판단: 정현우" in runbook
    assert "배포·Rollback 실행, 관제 총괄: 권가빈" in runbook
    assert "대체 배포·Rollback 실행자: 미정" in runbook
