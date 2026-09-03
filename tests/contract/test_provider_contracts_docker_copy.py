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


def test_production_worker_receives_required_environment() -> None:
    compose_file = PROJECT_ROOT / "infra" / "docker" / "docker-compose.prod.yml"
    worker_service = compose_file.read_text(encoding="utf-8").split("  ai-worker:\n", maxsplit=1)[1]
    worker_environment = worker_service.split("    environment:\n", maxsplit=1)[1].split("\n    restart:", maxsplit=1)[
        0
    ]

    assert "      ENV: ${ENV}" in worker_environment
