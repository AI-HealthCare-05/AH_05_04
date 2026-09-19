from pathlib import Path

import yaml


def test_checkin_deadline_service_is_opt_in_and_reuses_backend_image_without_provider_or_push_access():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    fastapi = compose["services"]["fastapi"]
    service = compose["services"]["checkin-deadline-scheduler"]
    assert service["profiles"] == ["checkin-deadlines"]
    assert service["image"] == fastapi["image"]
    assert service["entrypoint"] == ["/app/.venv/bin/python", "-m", "app.commands.schedule_checkin_deadlines"]
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
    assert not [key for key in service["environment"] if key.startswith("WEB_PUSH_")]
    assert not {
        "OPENAI_API_KEY",
        "CLOVA_OCR_SECRET",
        "SMTP_PASSWORD",
        "REDIS_PASSWORD",
    } & set(service["environment"])
    assert "ports" not in service and "volumes" not in service
    assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_checkin_deadline_runtime_is_independent_of_the_notification_profile():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    notifications = compose["services"]["notification-scheduler"]
    checkins = compose["services"]["checkin-deadline-scheduler"]
    assert set(notifications["profiles"]).isdisjoint(checkins["profiles"])
    assert notifications["entrypoint"] != checkins["entrypoint"]


def test_deployment_stops_checkin_deadline_scheduler_before_migration_and_does_not_auto_enable():
    script = Path("scripts/deployment.sh").read_text()
    stop = script.index("docker compose --profile checkin-deadlines stop -t 15 checkin-deadline-scheduler")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')
    assert stop < migration
    assert "checkin-deadline-scheduler)$" in script[stop:migration]
    assert "--profile checkin-deadlines up" not in script
