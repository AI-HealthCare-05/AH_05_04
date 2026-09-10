import os
import subprocess
import sys
from pathlib import Path

from coverage import CoverageData


def _run_worker_xdist_suite(
    test_root: Path,
    coverage_file: Path,
    worker_record_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(coverage_file)
    environment["PYTEST_ADDOPTS"] = ""
    if worker_record_dir is not None:
        environment["WORKER_RECORD_DIR"] = str(worker_record_dir)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "2",
            "--dist=loadfile",
            "--max-worker-restart=0",
            "--cov",
            "--cov-report=",
            "--basetemp",
            str(test_root / ".pytest-tmp"),
            str(test_root),
        ],
        cwd=test_root.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_worker_xdist_combines_coverage_from_distinct_workers(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    worker_record_dir = tmp_path / "worker-records"
    worker_record_dir.mkdir()
    (tmp_path / "worker_module_a.py").write_text("def covered_a():\n    return 'a'\n", encoding="utf-8")
    (tmp_path / "worker_module_b.py").write_text("def covered_b():\n    return 'b'\n", encoding="utf-8")
    (suite / "test_a.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from worker_module_a import covered_a\n\n"
        "def test_a():\n"
        "    (Path(os.environ['WORKER_RECORD_DIR']) / 'worker-a').write_text(os.environ['PYTEST_XDIST_WORKER'])\n"
        "    assert covered_a() == 'a'\n",
        encoding="utf-8",
    )
    (suite / "test_b.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from worker_module_b import covered_b\n\n"
        "def test_b():\n"
        "    (Path(os.environ['WORKER_RECORD_DIR']) / 'worker-b').write_text(os.environ['PYTEST_XDIST_WORKER'])\n"
        "    assert covered_b() == 'b'\n",
        encoding="utf-8",
    )
    coverage_file = tmp_path / ".coverage.worker"

    result = _run_worker_xdist_suite(suite, coverage_file, worker_record_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    worker_ids = {path.read_text(encoding="utf-8") for path in worker_record_dir.iterdir()}
    assert worker_ids == {"gw0", "gw1"}
    assert coverage_file.is_file(), [str(path) for path in tmp_path.rglob("*")]
    coverage_data = CoverageData(basename=str(coverage_file))
    coverage_data.read()
    measured_lines_by_file = {
        Path(path).name: set(coverage_data.lines(path) or []) for path in coverage_data.measured_files()
    }
    assert 2 in measured_lines_by_file["worker_module_a.py"]
    assert 2 in measured_lines_by_file["worker_module_b.py"]


def test_worker_xdist_propagates_a_test_failure(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_pass.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    (suite / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")

    result = _run_worker_xdist_suite(suite, tmp_path / ".coverage.worker")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed" in result.stdout
