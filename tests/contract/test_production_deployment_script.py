import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/deployment.sh"


def test_deployment_script_rejects_missing_redis_password_before_external_actions(tmp_path: Path) -> None:
    """ai_worker/core/config.py는 non-local(STAGING/PRODUCTION 공통) 환경에서 REDIS_PASSWORD
    실제 값을 요구한다(#150). PROD_ENV_FILE에서 이 값이 비어 있으면 compose가 빈 문자열로
    치환해 무인증 Redis가 뜰 수 있으므로, docker login/build/push 같은 외부 작업 전에
    배포 스크립트가 이를 필수 변수로 차단해야 한다(#321)."""
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "DB_ADMIN_USER=dummy_admin",
                "DB_ADMIN_PASSWORD=dummy-admin-password",
                "DB_MIGRATION_USER=dummy_migration",
                "DB_MIGRATION_PASSWORD=dummy-migration-password",
                "DB_APP_USER=dummy_app",
                "DB_APP_PASSWORD=dummy-app-password",
                # REDIS_PASSWORD는 의도적으로 생략한다.
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "PROD_ENV_FILE": str(env_file)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "REDIS_PASSWORD" in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_passes_redis_check_when_password_present(tmp_path: Path) -> None:
    """REDIS_PASSWORD가 채워져 있으면 Redis 검증은 통과하고, 그 다음 검증
    (DB_ADMIN_USER 등 역할 이름 중복 검사)으로 넘어가야 한다."""
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "DB_ADMIN_USER=dummy_admin",
                "DB_ADMIN_PASSWORD=dummy-admin-password",
                "DB_MIGRATION_USER=dummy_admin",
                "DB_MIGRATION_PASSWORD=dummy-migration-password",
                "DB_APP_USER=dummy_app",
                "DB_APP_PASSWORD=dummy-app-password",
                "REDIS_PASSWORD=dummy-redis-password",
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "PROD_ENV_FILE": str(env_file)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "REDIS_PASSWORD" not in completed.stdout
    assert "서로 다른 이름이어야 합니다" in completed.stdout
