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


def test_deployment_script_rejects_placeholder_redis_password_before_external_actions(tmp_path: Path) -> None:
    """REDIS_PASSWORD가 비어 있지 않더라도 envs/example.prod.env의 placeholder 값을 그대로
    쓰면 -z 검사를 통과해버린다. 그 상태로 배포되면 git에 커밋된 공개 비밀번호로 운영
    Redis가 인증을 걸고 뜬다. deploy-staging.sh와 동일하게 placeholder를 docker login 등
    외부 작업 전에 차단해야 한다(#321, 김지혜 리뷰)."""
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
                "REDIS_PASSWORD=replace-with-production-redis-password",
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
    assert "placeholder" in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_rejects_non_production_env_before_external_actions(tmp_path: Path) -> None:
    """PROD_ENV_FILE override를 열면서 PROD_ENV_FILE=envs/.local.env 같은 다른 환경파일로
    운영 배포 스크립트를 실행할 수 있게 됐다. deploy-staging.sh의 ENV 검사와 대칭으로,
    ENV가 production이 아니면 docker login 등 외부 작업 전에 차단해야 한다(#321, 김지혜 리뷰)."""
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
                "REDIS_PASSWORD=dummy-redis-password",
                "ENV=local",
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
    assert "ENV는 production이어야 합니다" in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_passes_redis_check_when_password_present(tmp_path: Path) -> None:
    """REDIS_PASSWORD가 채워져 있고 ENV=production이면 그 다음 검증
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
                "ENV=production",
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
