import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/deployment.sh"


def test_notification_service_is_opt_in_and_reuses_backend_image_without_provider_or_worker_access():
    compose = yaml.safe_load(Path("infra/docker/docker-compose.prod.yml").read_text())
    fastapi = compose["services"]["fastapi"]
    service = compose["services"]["notification-scheduler"]
    assert service["profiles"] == ["notifications"]
    assert service["image"] == fastapi["image"]
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
        "WEB_PUSH_ENABLED",
        "WEB_PUSH_PRODUCTION_ENABLED",
        "WEB_PUSH_ALLOWED_HOSTS",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "WEB_PUSH_VAPID_PUBLIC_KEY",
        "WEB_PUSH_VAPID_SUBJECT",
        "WEB_PUSH_ENCRYPTION_KEYS",
        "WEB_PUSH_ACTIVE_KEY_ID",
        "WEB_PUSH_ENDPOINT_HMAC_KEY",
    }
    assert service["environment"]["DB_USER"] == "${DB_APP_USER}"
    assert service["environment"]["DB_PASSWORD"] == "${DB_APP_PASSWORD}"
    assert service["environment"]["SQLALCHEMY_ECHO"] == "false"
    web_push_keys = {key for key in service["environment"] if key.startswith("WEB_PUSH_")}
    assert web_push_keys
    assert {key: service["environment"][key] for key in web_push_keys} == {
        key: fastapi["environment"][key] for key in web_push_keys
    }
    assert not {
        "OPENAI_API_KEY",
        "CLOVA_OCR_SECRET",
        "SMTP_PASSWORD",
        "REDIS_PASSWORD",
    } & set(service["environment"])
    assert "ports" not in service and "volumes" not in service
    assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_deployment_stops_notification_scheduler_before_migration_and_does_not_auto_enable():
    script = SCRIPT_PATH.read_text()
    record = script.index("notification_scheduler_was_running=false")
    stop = script.index("docker compose --profile notifications stop -t 15 notification-scheduler")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')

    # 기동 상태는 정지하기 전에 기록해야 배포 전 상태를 알 수 있다.
    assert record < stop < migration
    assert (
        "^(fastapi|ai-worker|source-writer|catalog-writer|candidate-index-builder"
        "|notification-scheduler|checkin-deadline-scheduler)$" in script[stop:migration]
    )

    # #434: opt-in 계약. 표준 배포 대상에 넣지 않는다.
    assert 'DEPLOY_SERVICES=("fastapi" "ai-worker" "nginx" "checkin-deadline-scheduler")' in script
    assert (
        "notification-scheduler"
        not in script[script.index("DEPLOY_SERVICES=(") : script.index("DEPLOY_SERVICES=(") + 120]
    )


def test_notification_scheduler_restart_is_guarded_by_the_recorded_pre_deployment_state():
    """#434: 무조건 기동이 아니라, 배포 전에 running이던 경우에만 되돌린다."""
    script = SCRIPT_PATH.read_text()
    deploy = script.index('"${deploy_services[@]}"')
    guard = script.index('if [ "$notification_scheduler_was_running" = true ]; then')
    restore = script.index("docker compose --profile notifications up")

    assert deploy < guard < restore
    # 복원은 반드시 guard 안에서만 일어난다.
    assert script.index('else\n  echo "notification-scheduler stays stopped') > restore


def test_notification_scheduler_restore_does_not_reopen_post_migration_dependencies():
    """#434: migration·DB head·역할 권한 검증을 마친 뒤의 상태 복구이므로 의존성을 다시 열지 않는다."""
    script = SCRIPT_PATH.read_text()
    restore = script.index("docker compose --profile notifications up")
    command = script[restore : script.index("notification-scheduler\n", restore)]

    # depends_on(postgres, migrate)을 다시 해석하면 직전 검증 경계를 재진입한다.
    assert "--no-deps" in command
    # 운영 Runbook의 수동 재생성 절차와 같은 형태를 유지한다.
    runbook = (PROJECT_ROOT / "docs/operations/notification-scheduler.md").read_text()
    assert "--no-deps notification-scheduler" in runbook


DOCKER_STUB = r"""#!/bin/bash
printf "%s\n" "$*" >> "$COMMAND_LOG"
if [[ "$*" == "wait migrate" ]]; then echo 0; fi
if [[ "$*" == *"exec -T postgres"* ]]; then
  printf "user\t1\nself_profile\t1\n"
  for name in medical_document_profile_null prescription_profile_null guide_profile_null \
      chat_session_profile_null prescription_profile_mismatch guide_profile_mismatch \
      chat_session_profile_mismatch; do
    printf "%s\t0\n" "$name"
  done
fi
if [[ "$*" == "compose ps --services --status running" ]]; then
  if grep -q -- "--wait notification-scheduler" "$COMMAND_LOG"; then
    printf "%s\n" "$RUNNING_AFTER_RESTORE"
  elif grep -q -- "--wait fastapi" "$COMMAND_LOG"; then
    printf "%s\n" "$RUNNING_AFTER_DEPLOY"
  elif grep -q -- "stop -t 15 notification-scheduler" "$COMMAND_LOG"; then
    :
  else
    printf "%s\n" "$RUNNING_BEFORE_DEPLOY"
  fi
fi
exit 0
"""


def _run_remote_deployment(
    tmp_path: Path,
    *,
    running_before_deploy: str,
    running_after_restore: str,
    running_after_deploy: str = "checkin-deadline-scheduler",
    deploy_services: str = "fastapi ai-worker nginx checkin-deadline-scheduler",
) -> subprocess.CompletedProcess[str]:
    """배포 스크립트의 원격 구간만 떼어 docker stub으로 실행한다."""
    remote = SCRIPT_PATH.read_text().split("bash -s\" <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_STUB)
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
            "DEPLOY_SERVICES": deploy_services,
            "COMMAND_LOG": str(tmp_path / "commands.log"),
            "RUNNING_BEFORE_DEPLOY": running_before_deploy,
            "RUNNING_AFTER_DEPLOY": running_after_deploy,
            "RUNNING_AFTER_RESTORE": running_after_restore,
        },
    )


def test_running_scheduler_is_running_again_after_deployment(tmp_path: Path) -> None:
    """running → deploy → running. migration 때문에 멈춘 알림을 방치하지 않는다."""
    result = _run_remote_deployment(
        tmp_path,
        running_before_deploy="checkin-deadline-scheduler\nnotification-scheduler",
        running_after_restore="checkin-deadline-scheduler\nnotification-scheduler",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "commands.log").read_text()
    restore_commands = [line for line in log.splitlines() if line.startswith("compose --profile notifications up")]
    assert restore_commands
    # 복원은 post-migration 의존성을 다시 열지 않는다.
    assert all("--no-deps" in command for command in restore_commands)
    assert "image prune" in log


def test_stopped_scheduler_stays_stopped_after_deployment(tmp_path: Path) -> None:
    """stopped → deploy → stopped. 일반 배포가 opt-in 알림을 활성화하지 않는다."""
    result = _run_remote_deployment(
        tmp_path,
        running_before_deploy="checkin-deadline-scheduler",
        running_after_restore="",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "commands.log").read_text()
    assert "--profile notifications up" not in log
    assert "notification-scheduler stays stopped" in result.stdout
    assert "image prune" in log


def test_checkin_deadline_scheduler_is_restored_even_when_notifications_stay_stopped(
    tmp_path: Path,
) -> None:
    """#839 복구 경로는 알림 활성화 여부와 무관하게 항상 유지된다."""
    result = _run_remote_deployment(
        tmp_path,
        running_before_deploy="",
        running_after_restore="",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "commands.log").read_text()
    assert "--wait fastapi ai-worker nginx checkin-deadline-scheduler" in log
    assert "checkin-deadline-scheduler is not running after deployment." not in result.stdout


@pytest.mark.parametrize("running_after_restore", ["", "checkin-deadline-scheduler"])
def test_deployment_fails_when_a_previously_running_scheduler_does_not_come_back(
    tmp_path: Path, running_after_restore: str
) -> None:
    """복원에 실패했는데 배포가 성공으로 끝나면 이번 장애가 그대로 재현된다."""
    result = _run_remote_deployment(
        tmp_path,
        running_before_deploy="notification-scheduler",
        running_after_restore=running_after_restore,
    )

    assert result.returncode == 1
    assert "notification-scheduler was running before deployment but is stopped now." in result.stdout
    assert "image prune" not in (tmp_path / "commands.log").read_text()
