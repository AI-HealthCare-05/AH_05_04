"""STORAGE_DIR contract 테스트가 공유하는 실제 Docker image 빌드 fixture입니다.

이미지 단독 실행 테스트와 Compose 실행(env_file only) 테스트가 같은 이미지를 재사용해
불필요한 중복 빌드를 피합니다.
"""

import shutil
import subprocess
from collections.abc import Iterator

import pytest

PROJECT_ROOT_DOCKERFILE = "backend/app/Dockerfile"
STORAGE_DIR_IMAGE_TAG = "ah-05-04-storage-dir-contract-test:latest"


@pytest.fixture(scope="session")
def storage_dir_built_image() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI가 없어 실제 이미지 빌드/실행을 검증할 수 없습니다.")

    subprocess.run(
        ["docker", "build", "-f", PROJECT_ROOT_DOCKERFILE, "-t", STORAGE_DIR_IMAGE_TAG, "."],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield STORAGE_DIR_IMAGE_TAG
    finally:
        subprocess.run(["docker", "rmi", "-f", STORAGE_DIR_IMAGE_TAG], check=False, capture_output=True, text=True)
