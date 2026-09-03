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
