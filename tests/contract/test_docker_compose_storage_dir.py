"""backend/ 폴더 분리 이후에도 STORAGE_DIR 기본 경로가 하위호환되는지 확인합니다.

기존 팀원의 .local.env에는 STORAGE_DIR이 없을 수 있습니다. 이 경우에도
docker-compose.yml이 컨테이너 내부 기본값을 `/app/uploads/medical_documents`로
보장해야 합니다(PR #87 리뷰).

Compose의 `${VAR:-default}` 보간은 서비스의 `env_file:`이 아니라 `docker compose`를
실행할 때 사용한 shell / `--env-file`을 기준으로 처리됩니다. YAML 문자열만 확인하는
검사는 이 우선순위를 검증하지 못하므로, 아래 두 테스트는 실제 `docker compose config`
렌더링 결과로 값이 없을 때의 기본값과 선택한 env 파일의 사용자 지정 값 보존을 확인합니다
(PR #87 리뷰 재지적).
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_STORAGE_DIR = "/app/uploads/medical_documents"


def _load_fastapi_service() -> dict:
    compose_config = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose_config["services"]["fastapi"]


def _storage_dir_expression() -> str:
    return _load_fastapi_service()["environment"]["STORAGE_DIR"]


def _render_storage_dir(tmp_path: Path, *, service_env_file: str, cli_env_file: str) -> str:
    """docker-compose.yml에 실제로 선언된 STORAGE_DIR 표현식을 최소 Compose 파일에 그대로
    옮겨 `docker compose config`로 렌더링한다. 개발자의 실제 envs/.local.env는 건드리지 않는다."""
    (tmp_path / "service.env").write_text(service_env_file, encoding="utf-8")
    (tmp_path / "cli.env").write_text(cli_env_file, encoding="utf-8")
    compose_path = tmp_path / "docker-compose.storage-dir.yml"
    compose_path.write_text(
        textwrap.dedent(f"""\
            services:
              fastapi:
                image: scratch
                env_file:
                  - service.env
                environment:
                  STORAGE_DIR: {_storage_dir_expression()}
            """),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "--env-file",
            str(tmp_path / "cli.env"),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = json.loads(completed.stdout)
    return rendered["services"]["fastapi"]["environment"]["STORAGE_DIR"]


@pytest.fixture(autouse=True)
def _require_docker_compose() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI가 없어 실제 docker compose config 렌더링을 검증할 수 없습니다.")


def test_docker_compose_config_defaults_storage_dir_when_unset_anywhere(tmp_path: Path) -> None:
    """service env_file과 --env-file 모두 STORAGE_DIR이 없으면 기존 경로가 기본값으로 렌더링된다."""
    resolved = _render_storage_dir(tmp_path, service_env_file="", cli_env_file="")

    assert resolved == LEGACY_STORAGE_DIR


def test_docker_compose_config_preserves_value_from_selected_env_file(tmp_path: Path) -> None:
    """`docker compose --env-file`로 선택한 env 파일에 사용자 지정 값이 있으면 그 값이 유지된다."""
    custom_storage_dir = "/custom/storage/path"
    resolved = _render_storage_dir(
        tmp_path,
        service_env_file=f"STORAGE_DIR={custom_storage_dir}\n",
        cli_env_file=f"STORAGE_DIR={custom_storage_dir}\n",
    )

    assert resolved == custom_storage_dir


def test_fastapi_service_defaults_storage_dir_when_env_file_omits_it() -> None:
    """STORAGE_DIR이 없는 .local.env로도 기존 경로가 유지되도록 기본값이 선언되어 있습니다."""
    fastapi_service = _load_fastapi_service()
    environment = fastapi_service.get("environment")

    assert environment is not None, "fastapi 서비스에 environment 키가 없습니다."

    storage_dir_default = environment.get("STORAGE_DIR")

    assert storage_dir_default is not None, "fastapi 서비스에 STORAGE_DIR 기본값이 선언되어 있지 않습니다."
    assert LEGACY_STORAGE_DIR in storage_dir_default, (
        f"STORAGE_DIR 기본값이 기존 경로({LEGACY_STORAGE_DIR})를 보존하지 않습니다: {storage_dir_default!r}"
    )
    # `${STORAGE_DIR:-...}` 형태여야 실제 .local.env 값이 있으면 그 값을 우선합니다.
    assert storage_dir_default.startswith("${STORAGE_DIR:-"), (
        f"STORAGE_DIR이 환경변수보다 하드코딩된 값을 우선하고 있습니다: {storage_dir_default!r}"
    )


def test_fastapi_dockerfile_copies_from_backend_app() -> None:
    """빌드 컨텍스트가 저장소 루트일 때 Dockerfile 경로가 backend/app을 가리킵니다."""
    fastapi_service = _load_fastapi_service()

    assert fastapi_service["build"]["dockerfile"] == "backend/app/Dockerfile"
