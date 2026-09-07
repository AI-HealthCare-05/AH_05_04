import re
import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = PROJECT_ROOT / "infra/docker/docker-compose.staging.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_staging_only_exposes_nginx_http_port() -> None:
    services = _compose()["services"]

    assert set(services) == {
        "postgres",
        "redis",
        "configure-db-roles",
        "migrate",
        "fastapi",
        "nginx",
    }
    for service_name in ("postgres", "redis", "configure-db-roles", "migrate", "fastapi"):
        assert not services[service_name].get("ports"), f"{service_name} must stay on the Docker network"

    assert services["nginx"]["ports"] == ["80:80"]
    assert "ai-worker" not in services


def test_staging_fastapi_uses_one_worker_and_safe_non_local_flags() -> None:
    fastapi = _compose()["services"]["fastapi"]
    command = fastapi["command"]
    environment = fastapi["environment"]

    workers_index = command.index("--workers")
    assert command[workers_index + 1] == "1"
    assert environment["ENV"] == "staging"
    assert environment["CHAT_HISTORY_CONTEXT_ENABLED"] == "false"
    assert environment["RELEASE_VALIDATION_ALLOWED"] == "false"
    assert environment["STORAGE_DIR"] == "/app/media/medical_documents"
    assert not fastapi.get("ports")


def test_staging_migration_and_health_gate_nginx_startup() -> None:
    services = _compose()["services"]

    assert services["migrate"]["depends_on"]["configure-db-roles"]["condition"] == ("service_completed_successfully")
    assert services["fastapi"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["nginx"]["depends_on"]["fastapi"]["condition"] == "service_healthy"
    assert "healthcheck" in services["fastapi"]


def test_staging_nginx_serves_spa_and_uses_ocr_safe_timeout() -> None:
    config = (PROJECT_ROOT / "infra/nginx/staging_http.conf").read_text(encoding="utf-8")
    timeout_match = re.search(r"proxy_read_timeout\s+(\d+)s;", config)

    assert timeout_match is not None
    assert int(timeout_match.group(1)) > 60
    assert "try_files $uri $uri/ /index.html;" in config
    assert "client_max_body_size 32m;" in config
    assert "server fastapi:8000;" in config


def test_frontend_production_image_builds_static_assets() -> None:
    dockerfile = (PROJECT_ROOT / "frontend/Dockerfile.prod").read_text(encoding="utf-8")

    assert "ARG VITE_API_BASE_URL" in dockerfile
    assert "pnpm build" in dockerfile
    assert "FROM nginx:1.27-alpine" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile


def test_frontend_reports_are_excluded_from_docker_build_context() -> None:
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "frontend/playwright-report" in dockerignore
    assert "frontend/test-results" in dockerignore


def test_staging_example_contains_no_real_secret_file_values() -> None:
    example = (PROJECT_ROOT / "envs/example.staging.env").read_text(encoding="utf-8")

    assert "ENV=staging" in example
    assert "OCR_STRUCTURE_LLM_ENABLED=false" in example
    assert "OPENAI_API_KEY=replace-with-" in example
    assert "CLOVA_OCR_SECRET=replace-with-" in example
    assert "STAGING_SSH_KEY_PATH=replace-with-absolute-path" in example


def test_staging_deploy_script_rejects_example_placeholders_before_external_actions() -> None:
    script = PROJECT_ROOT / "scripts/deploy-staging.sh"
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "STAGING_ENV_FILE": "envs/example.staging.env"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "placeholder" in completed.stderr
    assert script.stat().st_mode & 0o111
