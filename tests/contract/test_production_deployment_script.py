import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/deployment.sh"
VALID_SNAPSHOT_ENCRYPTION_KEY = "mNZgOOlYI_KL5_6HjgyDFGPkMW7xU7CBpPYY5awEaRg="


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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "ENV=production",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
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


def test_deployment_script_rejects_quoted_placeholder_redis_password(tmp_path: Path) -> None:
    """파일 원문 검사(`=(replace-with|replace_with)`)는 REDIS_PASSWORD="replace-with-..."처럼
    따옴표로 감싼 값을 놓친다. source 이후 따옴표가 제거된 실제 셸 변수 값도 검사해
    이 경로를 막아야 한다(#321, 김지혜 2차 리뷰 P1)."""
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "ENV=production",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
                'REDIS_PASSWORD="replace-with-production-redis-password"',
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


def test_deployment_script_rejects_env_file_missing_redis_password_even_when_inherited(tmp_path: Path) -> None:
    """source는 파일에 없는 변수를 초기화하지 않으므로, 실행 셸에 REDIS_PASSWORD가 이미
    있으면 파일에 값이 없어도 -z 검사를 통과한다. 하지만 원격 배포는 파일 원문만
    서버로 복사하므로, 로컬 검증과 실제 전송 설정이 어긋난다. 파일 자체의 선언 여부를
    확인해야 한다(#321, 김지혜 2차 리뷰 P2)."""
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "ENV=production",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
                # REDIS_PASSWORD는 파일에서 의도적으로 생략하고, 실행 셸에만 상속시킨다.
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PROD_ENV_FILE": str(env_file),
            "REDIS_PASSWORD": "dummy-inherited-password",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "REDIS_PASSWORD" in completed.stdout
    assert "선언되어 있지 않습니다" in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_rejects_env_file_missing_env_key_even_when_inherited(tmp_path: Path) -> None:
    """REDIS_PASSWORD와 같은 이유로, ENV도 실행 셸에서 상속되면 파일에 값이 없어도
    != production 검사를 통과한다. 원격에는 파일 원문만 전달되므로 파일 자체의
    선언 여부를 확인해야 한다(#321, 김지혜 2차 리뷰 P2)."""
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "REDIS_PASSWORD=dummy-redis-password",
                # ENV는 파일에서 의도적으로 생략하고, 실행 셸에만 상속시킨다.
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PROD_ENV_FILE": str(env_file),
            "ENV": "production",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "ENV" in completed.stdout
    assert "선언되어 있지 않습니다" in completed.stdout
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "REDIS_PASSWORD=dummy-redis-password",
                "ENV=local",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
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
                "SOURCE_WRITER_USER=dummy_writer",
                "SOURCE_WRITER_PASSWORD=dummy-writer-password",
                "REDIS_PASSWORD=dummy-redis-password",
                "ENV=production",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
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


def test_deployment_script_rejects_missing_snapshot_key_even_when_inherited(tmp_path: Path) -> None:
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "ENV=production",
                "REDIS_PASSWORD=dummy-redis-password",
                # active key는 파일에서 생략하고 실행 셸에만 상속시킨다.
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PROD_ENV_FILE": str(env_file),
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": VALID_SNAPSHOT_ENCRYPTION_KEY,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY" in completed.stdout
    assert "선언되어 있지 않습니다" in completed.stdout
    assert VALID_SNAPSHOT_ENCRYPTION_KEY not in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_rejects_empty_snapshot_key_before_external_actions(tmp_path: Path) -> None:
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "ENV=production",
                "REDIS_PASSWORD=dummy-redis-password",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY=",
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
    assert "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY" in completed.stdout
    assert "비어 있습니다" in completed.stdout
    assert "docker" not in completed.stdout.lower()


def test_deployment_script_rejects_snapshot_key_placeholder_before_external_actions(tmp_path: Path) -> None:
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "ENV=production",
                "REDIS_PASSWORD=dummy-redis-password",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
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
    assert "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY" in completed.stdout
    assert "placeholder" in completed.stdout
    assert "docker" not in completed.stdout.lower()


@pytest.mark.parametrize(
    ("writer_name", "writer_password", "message"),
    [
        ("", "synthetic-writer-secret", "SOURCE_WRITER_USER"),
        ("dummy_writer", "", "SOURCE_WRITER_PASSWORD"),
        ("dummy_app", "synthetic-writer-secret", "다른 이름"),
        ("dummy_writer", "replace-with-secret", "placeholder"),
    ],
)
def test_writer_credentials_are_validated_before_external_actions(tmp_path, writer_name, writer_password, message):
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            [
                "ENV=production",
                "REDIS_PASSWORD=synthetic-redis-secret",
                f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
                "DB_ADMIN_USER=dummy_admin",
                "DB_ADMIN_PASSWORD=synthetic-admin-secret",
                "DB_MIGRATION_USER=dummy_owner",
                "DB_MIGRATION_PASSWORD=synthetic-owner-secret",
                "DB_APP_USER=dummy_app",
                "DB_APP_PASSWORD=synthetic-app-secret",
                f'SOURCE_WRITER_USER="{writer_name}"',
                f'SOURCE_WRITER_PASSWORD="{writer_password}"',
                "",
            ]
        )
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "PROD_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert message in result.stdout
    assert "docker" not in result.stdout.lower()
    assert "synthetic-admin-secret" not in result.stdout + result.stderr
    assert "synthetic-writer-secret" not in result.stdout + result.stderr
