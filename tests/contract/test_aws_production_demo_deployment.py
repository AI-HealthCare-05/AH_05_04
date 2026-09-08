from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE_PATH = PROJECT_ROOT / "infra/docker/docker-compose.prod.yml"
NGINX_DIR = PROJECT_ROOT / "infra/nginx"


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


@pytest.mark.parametrize("config_name", ["prod_http.conf", "prod_https.conf"])
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


def test_production_scripts_use_env_domain_and_do_not_mutate_source_nginx_configs() -> None:
    deployment_script = _read(PROJECT_ROOT / "scripts/deployment.sh")
    certbot_script = _read(PROJECT_ROOT / "scripts/certbot.sh")

    assert 'expected_public_origin="https://${PRODUCTION_DOMAIN}"' in deployment_script
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


def test_production_example_declares_demo_origin_and_frontend_version() -> None:
    env_example = _read(PROJECT_ROOT / "envs/example.prod.env")

    for key in (
        "FRONTEND_VERSION",
        "PRODUCTION_DOMAIN",
        "PRODUCTION_PUBLIC_ORIGIN",
        "CERTBOT_EMAIL",
    ):
        assert f"{key}=" in env_example

    assert "COOKIE_DOMAIN=replace-with-production-domain.example" in env_example
    assert "CORS_ALLOWED_ORIGINS=https://replace-with-production-domain.example" in env_example
