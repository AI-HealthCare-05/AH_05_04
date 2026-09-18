"""PUBLIC_TRACK_C는 기본 차단이며 해제는 환경파일의 명시적 boolean으로만 가능하다.

docs/release-gates/post-mvp-1-external-approvals.md의 EXT-MED-001 · EXT-MED-002 ·
EXT-PRIV-002 · EXT-SAFETY-001이 승인되기 전까지 Track C는 실제 사용자에게 노출되지 않는다.
이 게이트는 Frontend build-time flag VITE_PUBLIC_TRACK_C로 구현되어 있으므로, 기본값이
열리거나 실행 셸 값이 승인을 대신하거나 gate 표현식이 조용히 제거되는 회귀를 막는다.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_EXPRESSION = "import.meta.env.VITE_PUBLIC_TRACK_C === 'true' || import.meta.env.DEV"


def test_build_arg_reaches_the_frontend_image() -> None:
    dockerfile = (ROOT / "frontend/Dockerfile.prod").read_text()
    script = (ROOT / "scripts/deployment.sh").read_text()
    assert "ARG VITE_PUBLIC_TRACK_C=false" in dockerfile
    assert "ENV VITE_PUBLIC_TRACK_C=$VITE_PUBLIC_TRACK_C" in dockerfile
    assert '"VITE_PUBLIC_TRACK_C=$VITE_PUBLIC_TRACK_C"' in script


def test_example_environments_keep_the_gate_closed() -> None:
    assert "VITE_PUBLIC_TRACK_C=false" in (ROOT / "envs/example.prod.env").read_text()
    assert "VITE_PUBLIC_TRACK_C=false" in (ROOT / "frontend/.env.example").read_text()


def test_running_shell_value_cannot_substitute_for_the_environment_file() -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    assert "unset VITE_PUBLIC_TRACK_C\n" in script
    assert script.index("unset VITE_PUBLIC_TRACK_C") < script.index('source "$PROD_ENV_FILE"')
    assert script.index("\nvalidate_public_track_c_configuration\n") < script.index(
        "for required_command in docker"
    )


@pytest.mark.parametrize("value", [None, "", "false", "true", "TRUE", "1", "yes"])
def test_gate_configuration_requires_explicit_boolean(tmp_path: Path, value: str | None) -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    start = script.index("validate_public_track_c_configuration() {")
    end = script.index("\n}", start) + 2
    env_file = tmp_path / "prod.env"
    env_file.write_text("" if value is None else f"VITE_PUBLIC_TRACK_C={value}\n")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu\nPROD_ENV_FILE="$1"\n'
            # 실행 셸에 열린 값이 있어도 환경파일의 누락을 대신하지 못한다.
            + 'VITE_PUBLIC_TRACK_C=true\nunset VITE_PUBLIC_TRACK_C\nsource "$PROD_ENV_FILE"\n'
            + script[start:end]
            + "\nvalidate_public_track_c_configuration\n"
            + 'printf "validated=%s" "$VITE_PUBLIC_TRACK_C"',
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


def test_frontend_entry_points_stay_behind_the_gate() -> None:
    """gate 표현식은 build 시점 상수 접기를 위해 두 파일에 그대로 있어야 한다."""
    router = (ROOT / "frontend/src/routes/AppRouter.tsx").read_text()
    schedule = (ROOT / "frontend/src/pages/SchedulePage.tsx").read_text()
    assert GATE_EXPRESSION in router
    assert GATE_EXPRESSION in schedule
    # Track C 진입 경로 세 곳이 모두 같은 상수를 거친다.
    assert "{enableTrackC && TrackCPage && <>" in router
    assert "const supportMedicationId = TRACK_C_PUBLIC &&" in schedule
    assert "{TRACK_C_PUBLIC && occurrence.status !== 'CANCELLED'" in schedule
    # 공개 시점에 사용자에게 /dev/ 경로가 노출되지 않는다.
    assert "/dev/track-c/" not in router
    assert "/dev/track-c/" not in schedule
