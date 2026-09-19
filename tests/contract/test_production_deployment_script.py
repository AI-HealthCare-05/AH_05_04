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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=false",
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


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("CLOVA_OCR_SECRET", "", "CLOVA_OCR_SECRET"),
        ("CLOVA_OCR_INVOKE_URL", "http://clova.test/ocr", "HTTPS"),
        ("PUBLIC_TRACK_F_ENABLED", "True", "PUBLIC_TRACK_F_ENABLED=false"),
        ("OCR_STRUCTURE_LLM_ENABLED", "true", "OCR_STRUCTURE_LLM_ENABLED=false"),
    ],
)
def test_worker_preflight_blocks_before_registry_and_ssh(tmp_path, key, value, expected):
    settings = {
        "ENV": "production",
        "REDIS_PASSWORD": "synthetic-redis",
        "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": VALID_SNAPSHOT_ENCRYPTION_KEY,
        "DB_ADMIN_USER": "admin",
        "DB_ADMIN_PASSWORD": "synthetic-admin",
        "DB_MIGRATION_USER": "migration",
        "DB_MIGRATION_PASSWORD": "synthetic-migration",
        "DB_APP_USER": "app",
        "DB_APP_PASSWORD": "synthetic-app",
        "SOURCE_WRITER_USER": "writer",
        "SOURCE_WRITER_PASSWORD": "synthetic-writer",
        "DOCKER_USER": "synthetic",
        "DOCKER_REPOSITORY": "demo",
        "APP_VERSION": "test123",
        "FRONTEND_VERSION": "test123",
        "AI_WORKER_VERSION": "test123",
        "TLS_TERMINATION": "cloudfront",
        "PRODUCTION_DOMAIN": "synthetic.cloudfront.net",
        "PRODUCTION_PUBLIC_ORIGIN": "https://synthetic.cloudfront.net",
        "COOKIE_DOMAIN": "synthetic.cloudfront.net",
        "CORS_ALLOWED_ORIGINS": "https://synthetic.cloudfront.net",
        "CLOUDFRONT_ORIGIN_VERIFY_SECRET": "synthetic-origin-secret-for-tests-only",
        "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
        "CLOVA_OCR_SECRET": "synthetic-clova-secret",
        "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED": "false",
    }
    settings[key] = value
    env_file = tmp_path / "prod.env"
    env_file.write_text("\n".join(f'{k}="{v}"' for k, v in settings.items()))
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin", "PROD_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert expected in result.stdout
    assert "synthetic-clova-secret" not in result.stdout + result.stderr
    assert "Docker login" not in result.stdout


@pytest.mark.parametrize("worker_health_exit", [0, 42])
def test_remote_deployment_waits_for_worker_and_propagates_readiness_failure(tmp_path, worker_health_exit):
    script = SCRIPT_PATH.read_text()
    remote = script.split("bash -s\" <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
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
        'if [[ "$*" == "compose up -d --pull always --wait fastapi ai-worker nginx checkin-deadline-scheduler" ]]; then\n'
        '  exit "$WORKER_HEALTH_EXIT"\n'
        "fi\n"
        # 배포 전 guard는 실행 중 서비스가 없어야 통과하고, 배포 후 검증은 기동을 확인한다(#839).
        'if [[ "$*" == "compose ps --services --status running" ]]; then\n'
        '  if grep -q -- "--wait fastapi" "$COMMAND_LOG"; then printf "%s\\n" "$RUNNING_AFTER_DEPLOY"; fi\n'
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(0o700)
    (tmp_path / "project").mkdir()
    log = tmp_path / "commands.log"
    result = subprocess.run(
        ["bash"],
        input=remote,
        text=True,
        capture_output=True,
        timeout=10,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "DEPLOY_SERVICES": "fastapi ai-worker nginx checkin-deadline-scheduler",
            "COMMAND_LOG": str(log),
            "WORKER_HEALTH_EXIT": str(worker_health_exit),
            "RUNNING_AFTER_DEPLOY": "checkin-deadline-scheduler",
        },
    )
    assert log.exists(), result.stderr
    commands = log.read_text()
    assert result.returncode == worker_health_exit
    assert commands.index("stop -t 90 fastapi ai-worker") < commands.index("--force-recreate migrate")
    assert commands.index("--entrypoint python fastapi") < commands.index("--wait fastapi ai-worker nginx")
    assert "checkin-deadline-scheduler" in commands.split("--wait fastapi ai-worker nginx", 1)[1]
    assert ("image prune" in commands) is (worker_health_exit == 0)


def _withdrawal_env_lines() -> list[str]:
    """회원탈퇴 gate 검증까지 도달하는 데 필요한 최소 운영 설정."""
    return [
        "ENV=production",
        "REDIS_PASSWORD=synthetic-redis-secret",
        f"IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY={VALID_SNAPSHOT_ENCRYPTION_KEY}",
        "DB_ADMIN_USER=dummy_admin",
        "DB_ADMIN_PASSWORD=synthetic-admin-secret",
        "DB_MIGRATION_USER=dummy_owner",
        "DB_MIGRATION_PASSWORD=synthetic-owner-secret",
        "DB_APP_USER=dummy_app",
        "DB_APP_PASSWORD=synthetic-app-secret",
        "SOURCE_WRITER_USER=dummy_writer",
        "SOURCE_WRITER_PASSWORD=synthetic-writer-secret",
    ]


def test_deployment_script_rejects_env_file_missing_account_withdrawal_gate(tmp_path: Path) -> None:
    """Compose는 ACCOUNT_WITHDRAWAL_REQUEST_ENABLED를 ${...:-false}로 치환한다(#825).
    .prod.env에 선언 자체가 없으면 배포는 성공하지만 컨테이너에는 false가 주입되어
    회원탈퇴 API가 503으로 fail-closed된다. 배포가 조용히 통과하지 않도록
    선언 여부를 외부 작업 전에 차단해야 한다."""
    env_file = tmp_path / "prod.env"
    # ACCOUNT_WITHDRAWAL_REQUEST_ENABLED는 의도적으로 생략한다.
    env_file.write_text("\n".join(_withdrawal_env_lines() + [""]), encoding="utf-8")

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
    assert "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED" in completed.stdout
    assert "docker" not in completed.stdout.lower()


@pytest.mark.parametrize(
    ("cleanup_role", "cleanup_password", "message"),
    [
        ("", "synthetic-cleanup-secret", "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE"),
        ("dummy_cleanup", "", "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD"),
    ],
)
def test_deployment_script_requires_cleanup_credentials_when_withdrawal_enabled(
    tmp_path: Path, cleanup_role: str, cleanup_password: str, message: str
) -> None:
    """Backend Config도 같은 조건을 validator로 막지만(#825), 그 실패는 image push와 원격
    compose 반영이 끝난 뒤 컨테이너 기동 시점에 드러난다. 외부 작업 전에 먼저 차단한다."""
    env_file = tmp_path / "prod.env"
    env_file.write_text(
        "\n".join(
            _withdrawal_env_lines()
            + [
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=true",
                f'ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE="{cleanup_role}"',
                f'ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD="{cleanup_password}"',
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
    assert message in completed.stdout
    assert "docker" not in completed.stdout.lower()
    assert "synthetic-cleanup-secret" not in completed.stdout + completed.stderr


def test_deployment_script_rejects_cleanup_credentials_inherited_from_parent_shell(tmp_path: Path) -> None:
    """source는 파일에 없는 변수를 초기화하지 않는다. 실행 셸에 cleanup role/password가
    export돼 있으면 .prod.env에 선언이 없어도 셸 값 검사를 통과해버리는데, 원격에는
    파일 원문만 복사되므로(하단 ssh) FastAPI 기동 시 Config validator(#825)에서 다시
    실패한다. 이 PR이 막으려는 silent configuration drift가 그대로 남으므로,
    파일의 직접 선언 여부도 외부 작업 전에 검사해야 한다."""
    env_file = tmp_path / "prod.env"
    # cleanup role/password는 의도적으로 파일에 선언하지 않는다.
    env_file.write_text(
        "\n".join(_withdrawal_env_lines() + ["ACCOUNT_WITHDRAWAL_REQUEST_ENABLED=true", ""]),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PROD_ENV_FILE": str(env_file),
            # 부모 셸에만 존재하는 값. 파일 선언을 대신할 수 없다.
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE": "inherited_cleanup_role",
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD": "inherited-cleanup-secret",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE" in completed.stdout
    assert "docker" not in completed.stdout.lower()
    assert "inherited-cleanup-secret" not in completed.stdout + completed.stderr
