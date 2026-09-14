from pathlib import Path

import yaml


def test_notification_service_is_opt_in_and_reuses_backend_image_without_provider_or_worker_access():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    service = compose["services"]["notification-scheduler"]
    assert service["profiles"] == ["notifications"]
    assert service["image"] == compose["services"]["fastapi"]["image"]
    assert service["entrypoint"] == ["/app/.venv/bin/python", "-m", "app.commands.schedule_notifications"]
    assert service["init"] is True and service["stop_grace_period"] == "15s"
    assert service["restart"] == "unless-stopped"
    assert set(service["environment"]) == {
        "ENV",
        "SECRET_KEY",
        "IDEMPOTENCY_HMAC_KEY",
        "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_CONNECTION_POOL_MAXSIZE",
        "SQLALCHEMY_ECHO",
    }
    assert service["environment"]["DB_USER"] == "${DB_APP_USER}"
    assert service["environment"]["DB_PASSWORD"] == "${DB_APP_PASSWORD}"
    assert service["environment"]["SQLALCHEMY_ECHO"] == "false"
    assert "ports" not in service and "volumes" not in service
    assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_deployment_stops_notification_scheduler_before_migration_and_does_not_auto_enable():
    script = Path("scripts/deployment.sh").read_text()
    stop = script.index("docker compose --profile notifications stop -t 15 notification-scheduler")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')
    assert stop < migration
    assert "^(fastapi|ai-worker|source-writer|notification-scheduler)$" in script[stop:migration]
    assert "--profile notifications up" not in script
