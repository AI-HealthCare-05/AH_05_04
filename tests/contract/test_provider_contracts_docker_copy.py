from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.parametrize(
    "dockerfile",
    [
        PROJECT_ROOT / "backend" / "app" / "Dockerfile",
        PROJECT_ROOT / "ai_worker" / "Dockerfile",
    ],
)
def test_image_copies_provider_contracts_package(dockerfile: Path) -> None:
    instructions = {
        line.strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "COPY ./provider_contracts ./provider_contracts" in instructions


@pytest.mark.parametrize(
    "dockerfile",
    [
        PROJECT_ROOT / "backend" / "app" / "Dockerfile",
        PROJECT_ROOT / "ai_worker" / "Dockerfile",
    ],
)
@pytest.mark.parametrize(
    ("package_name", "expected_instruction"),
    [
        ("ocr_runtime", "COPY ./ocr_runtime ./ocr_runtime"),
        (
            "provider_runtime",
            "COPY ./provider_runtime ./provider_runtime",
        ),
    ],
)
def test_images_copy_shared_runtime_packages(
    dockerfile: Path,
    package_name: str,
    expected_instruction: str,
) -> None:
    del package_name

    instructions = {
        line.strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert expected_instruction in instructions


def test_production_worker_receives_required_environment() -> None:
    compose_file = PROJECT_ROOT / "infra" / "docker" / "docker-compose.prod.yml"
    worker_service = compose_file.read_text(encoding="utf-8").split("  ai-worker:\n", maxsplit=1)[1]
    worker_environment = worker_service.split("    environment:\n", maxsplit=1)[1].split("\n    restart:", maxsplit=1)[
        0
    ]

    assert "      ENV: ${ENV}" in worker_environment
    assert "      CLOVA_OCR_INVOKE_URL: ${CLOVA_OCR_INVOKE_URL}" in worker_environment
    assert "      CLOVA_OCR_SECRET: ${CLOVA_OCR_SECRET}" in worker_environment
    assert "      CLOVA_OCR_TIMEOUT_SECONDS: ${CLOVA_OCR_TIMEOUT_SECONDS:-20}" in worker_environment
    assert "      STORAGE_DIR: ${STORAGE_DIR:-/app/media/medical_documents}" in worker_environment


def test_worker_mounts_shared_upload_storage_read_only() -> None:
    local_compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production_compose = (PROJECT_ROOT / "infra" / "docker" / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "      - medical_uploads:/app/uploads\n" in local_compose
    assert "      - medical_uploads:/app/uploads:ro\n" in local_compose
    assert "      - media_volume:/app/media\n" in production_compose
    assert "      - media_volume:/app/media:ro\n" in production_compose
