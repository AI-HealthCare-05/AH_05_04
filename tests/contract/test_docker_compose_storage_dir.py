"""backend/ 폴더 분리 이후에도 STORAGE_DIR 기본 경로가 하위호환되는지 확인합니다.

기존 팀원의 .local.env에는 STORAGE_DIR이 없을 수 있습니다. 이 경우에도
docker-compose.yml이 컨테이너 내부 기본값을 `/app/uploads/medical_documents`로
보장해야 합니다(PR #87 리뷰).
"""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_STORAGE_DIR = "/app/uploads/medical_documents"


def _load_fastapi_service() -> dict:
    compose_config = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose_config["services"]["fastapi"]


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
