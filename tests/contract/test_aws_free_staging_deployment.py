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


def test_staging_redis_requires_password() -> None:
    """ai_worker/core/config.py의 _validate_redis_password_for_non_local은 STAGING도
    PRODUCTION과 동일하게 REDIS_PASSWORD 실제 값을 요구한다. staging redis도 같은
    기준으로 인증을 강제해야 하며, 프로덕션과 마찬가지로 비밀번호를 --requirepass
    CLI 인자로 넘기면 ps aux/docker top/proc/<pid>/cmdline에 노출된다."""
    redis = _compose()["services"]["redis"]
    command = redis["command"]
    script = command[2]

    assert redis["environment"] == {"REDIS_PASSWORD": "${REDIS_PASSWORD}"}
    assert command[:2] == ["sh", "-c"]
    assert "--requirepass" not in script
    assert "requirepass $${REDIS_PASSWORD}" in script
    assert "exec redis-server /tmp/redis-runtime.conf" in script


def test_staging_redis_healthcheck_avoids_password_in_argv() -> None:
    redis = _compose()["services"]["redis"]
    healthcheck_command = redis["healthcheck"]["test"][1]

    assert " -a " not in healthcheck_command
    assert "REDISCLI_AUTH=" in healthcheck_command
    assert "REDIS_PASSWORD" in healthcheck_command


def test_staging_redis_runs_as_unprivileged_user() -> None:
    """sh -c 형태의 command는 redis 공식 이미지 entrypoint의 gosu 권한 하향 분기를 타지
    않아 명시하지 않으면 root로 기동된다(#322). 인증 경계가 된 프로세스는 비특권으로
    실행해야 한다."""
    redis = _compose()["services"]["redis"]

    assert redis["user"] == "redis"


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
    assert "REDIS_PASSWORD=replace-with-" in example


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


def test_staging_deploy_script_rejects_missing_redis_password_before_external_actions(tmp_path: Path) -> None:
    """staging Redis도 non-local REDIS_PASSWORD를 요구하므로(Major 리뷰 반영), 배포 스크립트가
    docker login/build/push나 ssh 같은 외부 작업 전에 이를 필수 변수로 차단해야 한다."""
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "\n".join(
            [
                "DOCKER_USER=dummy-docker-user",
                "DOCKER_REPOSITORY=dummy-repo",
                "APP_VERSION=1.0.0",
                "FRONTEND_VERSION=1.0.0",
                "STAGING_EC2_HOST=203.0.113.10",
                "STAGING_SSH_KEY_PATH=/tmp/dummy-key.pem",
                "STAGING_PUBLIC_ORIGIN=http://203.0.113.10",
                "ENV=staging",
                "SECRET_KEY=synthetic-secret-key-at-least-32-characters",
                "IDEMPOTENCY_HMAC_KEY=synthetic-idempotency-key-at-least-32-characters",
                "CORS_ALLOWED_ORIGINS=http://203.0.113.10",
                "DB_NAME=dummy_db",
                "DB_ADMIN_USER=dummy_admin",
                "DB_ADMIN_PASSWORD=dummy-admin-password",
                "DB_MIGRATION_USER=dummy_migration",
                "DB_MIGRATION_PASSWORD=dummy-migration-password",
                "DB_APP_USER=dummy_app",
                "DB_APP_PASSWORD=dummy-app-password",
                # REDIS_PASSWORD는 의도적으로 생략한다.
                "OPENAI_API_KEY=dummy-openai-key",
                "CLOVA_OCR_INVOKE_URL=https://dummy-clova.example",
                "CLOVA_OCR_SECRET=dummy-clova-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script = PROJECT_ROOT / "scripts/deploy-staging.sh"

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "STAGING_ENV_FILE": str(env_file)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "REDIS_PASSWORD" in completed.stderr
    assert "docker" not in completed.stdout.lower()
