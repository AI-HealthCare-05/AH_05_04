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


def test_terms_default_is_closed_and_explicit_setting_is_forwarded() -> None:
    dockerfile = (ROOT / "frontend/Dockerfile.prod").read_text()
    script = (ROOT / "scripts/deployment.sh").read_text()
    assert "ARG VITE_SIGNUP_TERMS_APPROVED=false" in dockerfile
    assert "ENV VITE_SIGNUP_TERMS_APPROVED=$VITE_SIGNUP_TERMS_APPROVED" in dockerfile
    assert '"VITE_SIGNUP_TERMS_APPROVED=$VITE_SIGNUP_TERMS_APPROVED"' in script
    assert "ENV VITE_EMAIL_VERIFICATION_ENABLED=$VITE_EMAIL_VERIFICATION_ENABLED" in dockerfile


@pytest.mark.parametrize("value", [None, "", "false", "true", "TRUE", "1", "yes"])
def test_terms_configuration_requires_explicit_boolean(tmp_path: Path, value: str | None) -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    start = script.index("validate_signup_terms_configuration() {")
    end = script.index("\n}", start) + 2
    env_file = tmp_path / "prod.env"
    env_file.write_text("" if value is None else f"VITE_SIGNUP_TERMS_APPROVED={value}\n")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu\nPROD_ENV_FILE="$1"\n'
            # Match the production source boundary; inherited approval must not substitute for a missing key.
            + 'VITE_SIGNUP_TERMS_APPROVED=true\nunset VITE_SIGNUP_TERMS_APPROVED\nsource "$PROD_ENV_FILE"\n'
            + script[start:end]
            + "\nvalidate_signup_terms_configuration\n"
            + 'printf "validated=%s" "$VITE_SIGNUP_TERMS_APPROVED"',
            "test",
            str(env_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (value in {"true", "false"})
    if value in {"true", "false"}:
        assert result.stdout == f"validated={value}"
    assert 'unset VITE_SIGNUP_TERMS_APPROVED\nset -a\nsource "$PROD_ENV_FILE"' in script
    assert script.index("\nvalidate_signup_terms_configuration\n") < script.index("for required_command in docker")
