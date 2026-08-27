"""backend/app/Dockerfile로 빌드한 이미지를 docker-compose 없이 단독 실행해도
STORAGE_DIR 기본값이 올바른지 확인합니다(PR #87 리뷰 재지적).

`backend/app/core/config.py`의 STORAGE_DIR 기본값은 `Path(__file__)` 기준으로 로컬
직접 실행(저장소 루트) 기준에 맞춰져 있습니다. 컨테이너 내부에서는 폴더 깊이가 달라
(`/app/app/core/config.py`) docker-compose 없이 이 이미지를 단독으로 실행하면
`/uploads/medical_documents`로 잘못 계산됩니다. 이미지 자체에 `ENV STORAGE_DIR=...`을
고정해 docker-compose 유무와 무관하게 항상 올바른 기본값을 갖는지, 그리고 명시적으로
넘긴 값은 여전히 우선하는지 실제 이미지 실행으로 확인합니다.
"""

import subprocess

LEGACY_STORAGE_DIR = "/app/uploads/medical_documents"
_PROBE_COMMAND = [
    "uv",
    "run",
    "--no-sync",
    "python",
    "-c",
    "from app.core.config import Config; print(Config().STORAGE_DIR)",
]
_REQUIRED_DB_ENV = {"DB_HOST": "x", "DB_USER": "x", "DB_PASSWORD": "x", "DB_NAME": "x"}


def _run_probe(image: str, *, extra_env: dict[str, str] | None = None) -> str:
    env_args = []
    for key, value in {**_REQUIRED_DB_ENV, **(extra_env or {})}.items():
        env_args += ["-e", f"{key}={value}"]

    completed = subprocess.run(
        ["docker", "run", "--rm", *env_args, image, *_PROBE_COMMAND],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_image_defaults_storage_dir_when_run_standalone_without_compose(storage_dir_built_image: str) -> None:
    """docker-compose 없이 이미지를 단독 실행해도 기존 경로가 유지된다."""
    resolved = _run_probe(storage_dir_built_image)

    assert resolved == LEGACY_STORAGE_DIR


def test_image_preserves_explicitly_provided_storage_dir(storage_dir_built_image: str) -> None:
    """이미지 기본값과 무관하게 명시적으로 전달한 STORAGE_DIR은 그대로 우선한다."""
    custom_storage_dir = "/custom/storage/path"
    resolved = _run_probe(storage_dir_built_image, extra_env={"STORAGE_DIR": custom_storage_dir})

    assert resolved == custom_storage_dir
