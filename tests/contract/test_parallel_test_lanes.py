import os
import re
import signal
import subprocess
import textwrap
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PARALLEL_LANES_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "parallel_test_lanes.sh"


def test_parallel_lane_runner_starts_all_lanes_before_waiting(tmp_path: Path) -> None:
    """A sequential implementation makes the first lane time out before the second starts."""
    script = textwrap.dedent(
        """
        source "$1"

        first_lane() {
          touch "$LANE_TEST_DIR/first.started"
          for _ in {1..100}; do
            if [ -f "$LANE_TEST_DIR/second.started" ]; then
              return 0
            fi
            sleep 0.01
          done
          return 11
        }

        second_lane() {
          touch "$LANE_TEST_DIR/second.started"
          for _ in {1..100}; do
            if [ -f "$LANE_TEST_DIR/first.started" ]; then
              return 0
            fi
            sleep 0.01
          done
          return 12
        }

        run_parallel_test_lanes first_lane second_lane
        """
    )
    environment = os.environ.copy()
    environment["LANE_TEST_DIR"] = str(tmp_path)

    result = subprocess.run(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "first.started").is_file()
    assert (tmp_path / "second.started").is_file()


def test_parallel_lane_runner_waits_for_every_lane_and_reports_any_failure(tmp_path: Path) -> None:
    """Losing an early failure behind a later success would make the test gate pass incorrectly."""
    script = textwrap.dedent(
        """
        source "$1"

        failing_lane() {
          touch "$LANE_TEST_DIR/failing.finished"
          return 7
        }

        successful_lane() {
          sleep 0.05
          touch "$LANE_TEST_DIR/successful.finished"
          return 0
        }

        run_parallel_test_lanes failing_lane successful_lane
        """
    )
    environment = os.environ.copy()
    environment["LANE_TEST_DIR"] = str(tmp_path)

    result = subprocess.run(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert (tmp_path / "failing.finished").is_file()
    assert (tmp_path / "successful.finished").is_file()
    assert "Test lane failed: failing_lane (exit 7)" in result.stdout
    assert "Test lane passed: successful_lane" in result.stdout


def test_parallel_lane_runner_groups_each_lane_output_when_log_directory_is_set(tmp_path: Path) -> None:
    """Direct concurrent stdout makes pytest progress from different suites unreadable."""
    log_dir = tmp_path / "logs"
    script = textwrap.dedent(
        """
        source "$1"

        first_lane() {
          echo "first begin"
          touch "$LANE_TEST_DIR/first.started"
          while [ ! -f "$LANE_TEST_DIR/second.finished" ]; do
            sleep 0.01
          done
          echo "first end"
        }

        second_lane() {
          while [ ! -f "$LANE_TEST_DIR/first.started" ]; do
            sleep 0.01
          done
          echo "second begin"
          touch "$LANE_TEST_DIR/second.finished"
          sleep 0.05
          echo "second end"
        }

        run_parallel_test_lanes first_lane second_lane
        """
    )
    environment = os.environ.copy()
    environment["LANE_TEST_DIR"] = str(tmp_path)
    environment["PARALLEL_TEST_LOG_DIR"] = str(log_dir)

    result = subprocess.run(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "first begin\nfirst end\nTest lane passed: first_lane\nsecond begin\nsecond end\nTest lane passed: second_lane"
    ) in result.stdout
    assert (log_dir / "first_lane.log").is_file()
    assert (log_dir / "second_lane.log").is_file()
    assert not re.search(r"\[\d+\].*\b(?:Done|Terminated)\b", result.stderr)


def test_parallel_lane_runner_forwards_interrupt_and_stops_lane_process_trees(tmp_path: Path) -> None:
    """CI 취소 시 lane의 pytest 자식 프로세스까지 즉시 종료되어야 합니다."""
    script = textwrap.dedent(
        """
        source "$1"

        child_loop() {
          lane_name="$1"
          trap 'touch "$LANE_TEST_DIR/$lane_name.stopped"; exit 0' TERM
          touch "$LANE_TEST_DIR/$lane_name.started"
          while true; do
            sleep 1
          done
        }

        first_lane() {
          child_loop first &
          wait "$!" || return "$?"
          touch "$LANE_TEST_DIR/first.finished"
        }

        second_lane() {
          child_loop second &
          wait "$!" || return "$?"
          touch "$LANE_TEST_DIR/second.finished"
        }

        run_parallel_test_lanes first_lane second_lane
        """
    )
    environment = os.environ.copy()
    environment["LANE_TEST_DIR"] = str(tmp_path)
    process = subprocess.Popen(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    started_paths = [tmp_path / "first.started", tmp_path / "second.started"]
    deadline = time.monotonic() + 3
    try:
        while time.monotonic() < deadline and not all(path.is_file() for path in started_paths):
            time.sleep(0.01)

        assert all(path.is_file() for path in started_paths), "lane child processes did not start"

        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=3)

        assert process.returncode == 128 + signal.SIGINT
        assert "Test lane passed:" not in stdout
        assert "Test lane interrupted: first_lane" in stdout
        assert "Test lane interrupted: second_lane" in stdout
        assert not re.search(r"\[\d+\].*\b(?:Done|Terminated)\b", stderr)
        assert not (tmp_path / "first.finished").exists()
        assert not (tmp_path / "second.finished").exists()
        stopped_paths = [tmp_path / "first.stopped", tmp_path / "second.stopped"]
        stopped_deadline = time.monotonic() + 1
        while time.monotonic() < stopped_deadline and not all(path.is_file() for path in stopped_paths):
            time.sleep(0.01)
        assert (tmp_path / "first.stopped").is_file()
        assert (tmp_path / "second.stopped").is_file()
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=3)


def test_parallel_lane_runner_restores_caller_traps_and_job_control_mode() -> None:
    script = textwrap.dedent(
        """
        source "$1"
        trap 'echo caller-int-trap' INT
        trap_before="$(trap -p INT)"

        successful_lane() {
          return 0
        }

        run_parallel_test_lanes successful_lane
        trap_after="$(trap -p INT)"

        [ "$trap_before" = "$trap_after" ]
        case "$-" in
          *m*) exit 21 ;;
        esac
        """
    )

    result = subprocess.run(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_parallel_lane_runner_suppresses_job_notifications_and_restores_enabled_monitor_mode() -> None:
    script = textwrap.dedent(
        """
        source "$1"
        set -m

        successful_lane() {
          return 0
        }

        run_parallel_test_lanes successful_lane
        case "$-" in
          *m*) exit 0 ;;
          *) exit 21 ;;
        esac
        """
    )

    result = subprocess.run(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not re.search(r"\[\d+\].*\b(?:Done|Terminated)\b", result.stderr)


def test_production_wrapper_preserves_interrupt_during_first_lane_registration(tmp_path: Path) -> None:
    """`set -u` caller가 첫 PID 등록 직전에 취소되어도 새 lane을 시작하거나 1로 바꾸면 안 됩니다."""
    script = textwrap.dedent(
        """
        set -euo pipefail
        set -T
        source "$1"
        controller_pid="$$"

        first_lane() {
          trap 'touch "$LANE_TEST_DIR/first.stopped"; exit 0' TERM
          touch "$LANE_TEST_DIR/first.started"
          while true; do
            sleep 1
          done
        }

        second_lane() {
          touch "$LANE_TEST_DIR/second.started"
        }

        inject_interrupt_before_first_pid_capture() {
          if [ "${BASH_COMMAND:-}" = 'lane_pids+=("$!")' ] && [ ! -f "$LANE_TEST_DIR/interrupted" ]; then
            for _ in {1..100}; do
              if [ -f "$LANE_TEST_DIR/first.started" ]; then
                break
              fi
              sleep 0.01
            done
            touch "$LANE_TEST_DIR/interrupted"
            kill -INT "$controller_pid"
          fi
        }
        trap inject_interrupt_before_first_pid_capture DEBUG

        run_parallel_test_lanes_with_failure_summary first_lane second_lane
        """
    )
    environment = os.environ.copy()
    environment["LANE_TEST_DIR"] = str(tmp_path)

    process = subprocess.Popen(
        ["bash", "-c", script, "parallel-test", str(PARALLEL_LANES_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        stdout, stderr = process.communicate(timeout=3)

        assert process.returncode == 128 + signal.SIGINT, stdout + stderr
        assert (tmp_path / "interrupted").is_file()
        assert (tmp_path / "first.started").is_file()
        assert (tmp_path / "first.stopped").is_file()
        assert not (tmp_path / "second.started").exists()
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=3)
