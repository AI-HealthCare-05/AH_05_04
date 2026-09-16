"""Production signup flags must reach the image build, including explicit opt-out."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("approved", ["true", "false"])
def test_frontend_build_forwards_signup_flags_without_splitting(approved: str) -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    start = script.index("build_and_push() {")
    end = script.index("\n}", start) + 2
    function = script[start:end]
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -eu\nCOLOR_BLUE= COLOR_GREEN= COLOR_NC=\n"
            "docker() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' \"$@\"; }\n"
            + function
            + "\nbuild_and_push registry repo Frontend version frontend/Dockerfile.prod . "
            + f'"VITE_API_BASE_URL=https://example.test" "VITE_SIGNUP_TERMS_APPROVED={approved}" '
            + '"VITE_EMAIL_VERIFICATION_ENABLED=false"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    calls = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("[")]
    build = calls[0]
    assert build[0] == "build"
    assert build.count("--build-arg") == 3
    assert f"VITE_SIGNUP_TERMS_APPROVED={approved}" in build
    assert "VITE_EMAIL_VERIFICATION_ENABLED=false" in build
    assert calls[1] == ["push", "registry/repo:frontend-version"]


def test_approved_terms_default_is_wired_into_production_build() -> None:
    dockerfile = (ROOT / "frontend/Dockerfile.prod").read_text()
    script = (ROOT / "scripts/deployment.sh").read_text()
    assert "ARG VITE_SIGNUP_TERMS_APPROVED=true" in dockerfile
    assert "ENV VITE_SIGNUP_TERMS_APPROVED=$VITE_SIGNUP_TERMS_APPROVED" in dockerfile
    assert '"VITE_SIGNUP_TERMS_APPROVED=${VITE_SIGNUP_TERMS_APPROVED:-true}"' in script
    assert "ENV VITE_EMAIL_VERIFICATION_ENABLED=$VITE_EMAIL_VERIFICATION_ENABLED" in dockerfile
