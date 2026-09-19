import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/deployment.sh"


def test_checkin_deadline_service_reuses_backend_image_without_provider_or_push_access():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    fastapi = compose["services"]["fastapi"]
    service = compose["services"]["checkin-deadline-scheduler"]
    # #839: Check-in 상태 정합성에 필요한 core runtime이므로 opt-in profile을 두지 않는다.
    assert "profiles" not in service
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


def test_checkin_deadline_runtime_starts_without_the_notification_profile():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    notifications = compose["services"]["notification-scheduler"]
    checkins = compose["services"]["checkin-deadline-scheduler"]
    # 알림은 opt-in으로 남고, 기한 처리는 알림 기동 여부와 무관하게 실행된다.
    assert notifications["profiles"] == ["notifications"]
    assert "profiles" not in checkins
    assert notifications["entrypoint"] != checkins["entrypoint"]


def test_deployment_stops_checkin_deadline_scheduler_before_migration():
    script = Path("scripts/deployment.sh").read_text()
    stop = script.index("docker compose stop -t 15 checkin-deadline-scheduler")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')
    assert stop < migration
    assert "checkin-deadline-scheduler)$" in script[stop:migration]


def test_deployment_deploys_checkin_deadline_scheduler_and_verifies_it_is_running():
    script = Path("scripts/deployment.sh").read_text()
    assert 'DEPLOY_SERVICES=("fastapi" "ai-worker" "nginx" "checkin-deadline-scheduler")' in script

    # #839: 기동 검증은 서비스를 올린 뒤에 수행하고, 실패하면 배포를 중단한다.
    deploy = script.index('"${deploy_services[@]}"')
    verification = script.index("checkin-deadline-scheduler is not running after deployment.")
    assert deploy < verification
    tail = script[verification:]
    assert "exit 1" in tail[: tail.index("docker image prune -f")]


def _run_remote_deployment(tmp_path: Path, *, running_after_deploy: str) -> subprocess.CompletedProcess[str]:
    """배포 스크립트의 원격 구간만 떼어 docker stub으로 실행한다."""
    remote = SCRIPT_PATH.read_text().split("bash -s\" <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [[ "$*" == "wait migrate" ]]; then echo 0; fi\n'
        'if [[ "$*" == *"exec -T postgres"* ]]; then\n'
        '  printf "user\\t1\\nself_profile\\t1\\n"\n'
        "  for name in medical_document_profile_null prescription_profile_null guide_profile_null "
        "chat_session_profile_null prescription_profile_mismatch guide_profile_mismatch chat_session_profile_mismatch; do\n"
        '    printf "%s\\t0\\n" "$name"\n'
        "  done\n"
        "fi\n"
        'if [[ "$*" == "compose ps --services --status running" ]]; then\n'
        '  if grep -q -- "--wait fastapi" "$COMMAND_LOG"; then printf "%s\\n" "$RUNNING_AFTER_DEPLOY"; fi\n'
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(0o700)
    (tmp_path / "project").mkdir()
    return subprocess.run(
        ["bash"],
        input=remote,
        text=True,
        capture_output=True,
        timeout=10,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "DEPLOY_SERVICES": "fastapi ai-worker nginx checkin-deadline-scheduler",
            "COMMAND_LOG": str(tmp_path / "commands.log"),
            "RUNNING_AFTER_DEPLOY": running_after_deploy,
        },
    )


def test_deployment_succeeds_when_checkin_deadline_scheduler_is_running(tmp_path: Path) -> None:
    result = _run_remote_deployment(tmp_path, running_after_deploy="checkin-deadline-scheduler")

    assert result.returncode == 0, result.stdout
    assert "image prune" in (tmp_path / "commands.log").read_text()


@pytest.mark.parametrize("running_after_deploy", ["", "fastapi"])
def test_deployment_fails_when_checkin_deadline_scheduler_is_not_running(
    tmp_path: Path, running_after_deploy: str
) -> None:
    """#839: scheduler가 멈춘 채 배포가 끝나면 기한 지난 occurrence가 PENDING으로 남는다."""
    result = _run_remote_deployment(tmp_path, running_after_deploy=running_after_deploy)

    assert result.returncode == 1
    assert "checkin-deadline-scheduler is not running after deployment." in result.stdout
    assert "image prune" not in (tmp_path / "commands.log").read_text()
