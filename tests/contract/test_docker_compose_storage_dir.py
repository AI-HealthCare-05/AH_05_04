"""backend/ 폴더 분리 이후에도 STORAGE_DIR 기본 경로가 하위호환되는지 확인합니다.

기존 팀원의 `.local.env`에는 STORAGE_DIR이 없을 수 있습니다. 이 경우에도 컨테이너
내부 기본값이 `/app/uploads/medical_documents`로 유지되어야 합니다(PR #87 리뷰).

`docker-compose.yml`의 `fastapi` 서비스는 `environment.STORAGE_DIR`을 선언하지
않습니다. 이 값을 다시 선언하면 `${VAR:-default}` 보간이 서비스의 `env_file:`이
아니라 `docker compose`를 실행한 shell/`--env-file` 기준으로 처리되어, `.local.env`
(env_file)에만 사용자 지정 값이 있는 실행 경로에서 그 값을 조용히 덮어씁니다
(PR #87 리뷰 재지적). 아래 테스트는 `docker run --env-file`로 Compose의 `env_file:`
동작을 동일하게 재현해 실제 image 실행 결과로 이 경로를 검증합니다.
"""

import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_STORAGE_DIR = "/app/uploads/medical_documents"
_PROBE_COMMAND = [
    "uv",
    "run",
    "--no-sync",
    "python",
    "-c",
    "from app.core.config import Config; print(Config().STORAGE_DIR)",
]
_REQUIRED_DB_ENV = "DB_HOST=x\nDB_USER=x\nDB_PASSWORD=x\nDB_NAME=x\n"


def _load_fastapi_service() -> dict:
    compose_config = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose_config["services"]["fastapi"]


def _run_probe_with_env_file(image: str, tmp_path: Path, *, env_file_body: str) -> str:
    """`docker run --env-file`로 Compose 서비스의 `env_file:` 적용 방식을 동일하게 재현한다."""
    env_file = tmp_path / "service.env"
    env_file.write_text(_REQUIRED_DB_ENV + env_file_body, encoding="utf-8")

    completed = subprocess.run(
        ["docker", "run", "--rm", "--env-file", str(env_file), image, *_PROBE_COMMAND],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_fastapi_service_declares_no_storage_dir_environment_override() -> None:
    """fastapi 서비스가 environment.STORAGE_DIR을 다시 선언하지 않는지 확인한다.

    이 override가 되살아나면 env_file에만 있는 사용자 값을 조용히 덮어쓰는
    회귀가 재발한다(PR #87 리뷰).
    """
    fastapi_service = _load_fastapi_service()
    environment = fastapi_service.get("environment")

    if environment is not None:
        assert "STORAGE_DIR" not in environment, (
            "fastapi 서비스가 environment.STORAGE_DIR을 다시 선언하고 있습니다. "
            "env_file(.local.env)에만 있는 사용자 값을 덮어쓰는 회귀가 재발합니다."
        )


def test_fastapi_service_declares_local_env_file() -> None:
    """fastapi 서비스가 `envs/.local.env`를 `env_file`로 선언하는지 확인한다.

    `docker run --env-file` 기반 테스트는 매번 직접 만든 env 파일을 사용하므로,
    `docker-compose.yml`에서 이 `env_file:` 선언 자체가 실수로 삭제돼도 잡지 못한다.
    이 테스트는 Compose YAML 구조를 직접 확인해 그 회귀를 잡는다(PR #87 리뷰 재지적).
    """
    fastapi_service = _load_fastapi_service()
    env_files = fastapi_service.get("env_file")

    assert env_files is not None, "fastapi 서비스에 env_file 선언이 없습니다."
    assert "./envs/.local.env" in env_files, (
        f"fastapi 서비스의 env_file이 envs/.local.env를 가리키지 않습니다: {env_files!r}"
    )


def test_fastapi_dockerfile_copies_from_backend_app() -> None:
    """빌드 컨텍스트가 저장소 루트일 때 Dockerfile 경로가 backend/app을 가리킵니다."""
    fastapi_service = _load_fastapi_service()

    assert fastapi_service["build"]["dockerfile"] == "backend/app/Dockerfile"


def test_compose_style_env_file_without_storage_dir_falls_back_to_legacy_default(
    storage_dir_built_image: str, tmp_path: Path
) -> None:
    """env_file(.local.env 상당)에 STORAGE_DIR이 없으면 Dockerfile의 이미지 기본값이 적용된다."""
    resolved = _run_probe_with_env_file(storage_dir_built_image, tmp_path, env_file_body="")

    assert resolved == LEGACY_STORAGE_DIR


def test_compose_style_env_file_only_storage_dir_is_preserved(storage_dir_built_image: str, tmp_path: Path) -> None:
    """env_file(.local.env 상당)에만 STORAGE_DIR이 있으면 그 값이 그대로 유지된다.

    environment 오버라이드가 없으므로 env_file 값이 image 기본값에 덮어써지지 않아야 한다
    (PR #87 리뷰 재지적 시나리오).
    """
    custom_storage_dir = "/custom/storage/path"
    resolved = _run_probe_with_env_file(
        storage_dir_built_image,
        tmp_path,
        env_file_body=f"STORAGE_DIR={custom_storage_dir}\n",
    )

    assert resolved == custom_storage_dir
