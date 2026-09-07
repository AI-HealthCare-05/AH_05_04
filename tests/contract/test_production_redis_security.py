from pathlib import Path

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE_PATH = PROJECT_ROOT / "infra/docker/docker-compose.prod.yml"
PRODUCTION_ENV_EXAMPLE_PATH = PROJECT_ROOT / "envs/example.prod.env"


def _compose() -> dict:
    return yaml.safe_load(PRODUCTION_COMPOSE_PATH.read_text(encoding="utf-8"))


def test_production_redis_is_not_published_to_host() -> None:
    redis = _compose()["services"]["redis"]

    assert "ports" not in redis
    assert redis["networks"] == ["ws"]


def test_production_redis_requires_password() -> None:
    redis = _compose()["services"]["redis"]
    command = redis["command"]
    script = command[2]

    assert redis["environment"] == {"REDIS_PASSWORD": "${REDIS_PASSWORD}"}
    assert command[:2] == ["sh", "-c"]
    # requirepass 값은 conf 파일 경로로만 redis-server의 argv에 전달되어야 한다. --requirepass처럼
    # 비밀번호를 직접 CLI 인자로 넘기면 `ps aux`/`docker top`/`/proc/<pid>/cmdline`에 평문이 남는다.
    assert "--requirepass" not in script
    assert "requirepass $${REDIS_PASSWORD}" in script
    assert "exec redis-server /tmp/redis-runtime.conf" in script


def test_production_redis_healthcheck_avoids_password_in_argv() -> None:
    redis = _compose()["services"]["redis"]
    healthcheck_command = redis["healthcheck"]["test"][1]

    # redis-cli -a "<password>"도 redis-cli 프로세스의 argv에 비밀번호를 노출한다.
    # REDISCLI_AUTH 환경변수 경유로 같은 위험 없이 인증해야 한다.
    assert " -a " not in healthcheck_command
    assert "REDISCLI_AUTH=" in healthcheck_command
    assert "REDIS_PASSWORD" in healthcheck_command


def test_production_ai_worker_receives_redis_settings() -> None:
    environment = _compose()["services"]["ai-worker"]["environment"]

    assert environment["REDIS_HOST"] == "${REDIS_HOST:-redis}"
    assert environment["REDIS_PORT"] == "${REDIS_PORT:-6379}"
    assert environment["REDIS_PASSWORD"] == "${REDIS_PASSWORD}"
    assert environment["REDIS_STREAM_NAME"] == "${REDIS_STREAM_NAME:-oryak:jobs}"
    assert environment["REDIS_DLQ_STREAM_NAME"] == "${REDIS_DLQ_STREAM_NAME:-oryak:jobs:dead-letter}"
    assert environment["REDIS_CONSUMER_GROUP"] == "${REDIS_CONSUMER_GROUP:-ai-workers}"


def test_production_env_example_declares_placeholder_redis_password() -> None:
    example = PRODUCTION_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    assert "REDIS_HOST=redis" in example
    assert "REDIS_PORT=6379" in example
    assert "REDIS_PASSWORD=replace-with-production-redis-password" in example
    assert "REDIS_STREAM_NAME=oryak:jobs" in example
