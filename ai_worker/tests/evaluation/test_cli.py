from __future__ import annotations

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation import cli as cli_module
from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.cli import main, publish_receipt_no_clobber
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import semantic_content_hash
from ai_worker.tasks.evaluation.retrieval_replay import build_adapter_registry
from ai_worker.tasks.evaluation.schemas.artifacts import ContentManifest, MetricResults, RagEvaluationRun, SuiteResults
from ai_worker.tests.evaluation.test_config import _manifest_payload, _resolved_for_manifest
from ai_worker.tests.evaluation.test_runner import CountingAdapter, StaticRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FOUNDATION_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"


class FixedClock:
    def __init__(self, timestamp: str) -> None:
        self.timestamp = timestamp

    def __call__(self) -> str:
        return self.timestamp


def test_cli_validates_fixture_without_creating_release_artifacts(tmp_path: Path) -> None:
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 0
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert (receipt["execution_status"], receipt["decision_status"]) == ("COMPLETED", "N/A")
    assert receipt["release_eligible"] is False
    assert receipt["manifest_path"] == "retrieval/manifests/dev-foundation-v1.dataset.json"
    assert receipt["error_codes"] == []
    assert (
        not {
            "run_id",
            "metrics",
            "gate",
            "passed",
            "provider",
            "report",
        }
        & receipt.keys()
    )


def test_cli_maps_unexpected_loader_eio_to_safe_internal_error_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = tmp_path / "receipt.json"

    def fail_loader(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.EIO, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module, "load_dataset", fail_loader)

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 1
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert receipt["execution_status"] == "ERROR"
    assert receipt["decision_status"] is None
    assert receipt["error_codes"] == [EvaluationErrorCode.INTERNAL_ERROR.value]
    assert "SENSITIVE_SENTINEL" not in result.read_text(encoding="utf-8")
    assert capsys.readouterr().err == f"{EvaluationErrorCode.INTERNAL_ERROR.value}\n"


def test_cli_does_not_overwrite_existing_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = tmp_path / "receipt.json"
    result.write_bytes(b"existing")

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    assert result.read_bytes() == b"existing"
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESULT_PATH_CONFLICT.value}\n"


def test_cli_rejects_existing_lock_without_removing_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = tmp_path / "receipt.json"
    lock = tmp_path / "receipt.json.lock"
    lock.write_bytes(b"operator-owned-stale-lock")

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    assert not result.exists()
    assert lock.read_bytes() == b"operator-owned-stale-lock"
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESULT_PATH_CONFLICT.value}\n"


def test_cli_rejects_symlink_parent_outside_allowed_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    (allowed_root / "linked").symlink_to(outside, target_is_directory=True)
    result = allowed_root / "linked/receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=allowed_root,
    )

    assert exit_code == 2
    assert not (outside / "receipt.json").exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_PATH_INVALID.value}\n"


def test_publish_receipt_uses_private_mode_and_cleans_temporary_files(tmp_path: Path) -> None:
    destination = tmp_path / "receipt.json"

    publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert destination.read_bytes() == b'{"safe":true}'
    assert destination.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["receipt.json"]


def test_publish_receipt_fails_closed_when_hard_link_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"

    def unsupported_link(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target, src_dir_fd, dst_dir_fd, follow_symlinks
        raise NotImplementedError

    monkeypatch.setattr(cli_module.os, "link", unsupported_link)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_receipt_cleans_lock_and_temp_after_short_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"
    real_write = os.write
    write_count = 0

    def short_second_write(descriptor: int, payload: bytes) -> int:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            return 0
        return real_write(descriptor, payload)

    monkeypatch.setattr(cli_module.os, "write", short_second_write)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert "SENSITIVE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_result_outside_injected_allowed_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    result = tmp_path / "outside.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=allowed_root,
    )

    assert exit_code == 2
    assert not result.exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_PATH_INVALID.value}\n"


def test_cli_writes_invalid_receipt_for_loader_validation_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_manifest = REPOSITORY_ROOT / "evals/retrieval/manifests/missing.dataset.json"
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(missing_manifest), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert (receipt["execution_status"], receipt["decision_status"]) == ("INVALID", None)
    assert receipt["error_codes"] == [EvaluationErrorCode.RESOURCE_MISSING.value]
    assert receipt["invalid_resource_paths"] == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_MISSING.value}\n"


def test_cli_does_not_echo_sensitive_manifest_filename(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_manifest = REPOSITORY_ROOT / "evals/retrieval/manifests/patient@example.com.dataset.json"
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(sensitive_manifest), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    receipt_bytes = result.read_bytes()
    assert b"patient@example.com" not in receipt_bytes
    receipt = json.loads(receipt_bytes)
    assert receipt["manifest_path"] == "unresolved.dataset.json"
    assert receipt["error_codes"] == [EvaluationErrorCode.PRIVACY_VALUE_DETECTED.value]
    assert "patient@example.com" not in capsys.readouterr().err


def test_publish_receipt_rejects_nonfinal_ancestor_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    original_ancestor = allowed / "level-one"
    destination = original_ancestor / "level-two/receipt.json"
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "level-two").mkdir(parents=True)
    detached = allowed / "detached-level-one"
    real_open = os.open
    swapped = False

    def swap_before_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and (Path(path) == destination.parent or os.fspath(path) == "level-one"):
            original_ancestor.rename(detached)
            original_ancestor.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(cli_module.os, "open", swap_before_open)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID
    assert not (outside / "level-two/receipt.json").exists()


def test_publish_receipt_preserves_replacement_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"
    replacement = b"replacement-lock"

    def replace_lock_then_fail(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, follow_symlinks
        assert src_dir_fd is not None and dst_dir_fd is not None
        lock_name = f"{os.fspath(target)}.lock"
        os.unlink(lock_name, dir_fd=dst_dir_fd)
        descriptor = os.open(lock_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dst_dir_fd)
        try:
            os.write(descriptor, replacement)
        finally:
            os.close(descriptor)
        raise OSError(errno.EIO, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module.os, "link", replace_lock_then_fail)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert "SENSITIVE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert (tmp_path / "receipt.json.lock").read_bytes() == replacement
    assert not destination.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["receipt.json.lock"]


def test_publish_receipt_preserves_replacement_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"
    replacement = b"replacement-temp"

    def replace_temp_then_fail(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del target, follow_symlinks
        assert src_dir_fd is not None and dst_dir_fd is not None
        temporary_name = os.fspath(source)
        os.unlink(temporary_name, dir_fd=src_dir_fd)
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=src_dir_fd,
        )
        try:
            os.write(descriptor, replacement)
        finally:
            os.close(descriptor)
        raise OSError(errno.EIO, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module.os, "link", replace_temp_then_fail)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert "SENSITIVE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 1
    assert ".tmp." in remaining[0].name
    assert remaining[0].read_bytes() == replacement
    assert not destination.exists()


def test_cli_maps_publication_path_race_to_exit_two_without_raw_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = tmp_path / "receipt.json"

    def path_race(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target, src_dir_fd, dst_dir_fd, follow_symlinks
        raise OSError(errno.ELOOP, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module.os, "link", path_race)

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    assert not result.exists()
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_PATH_INVALID.value}\n"


@pytest.mark.parametrize(
    "unsupported_errno",
    sorted({errno.ENOSYS, getattr(errno, "ENOTSUP", errno.ENOSYS), getattr(errno, "EOPNOTSUPP", errno.ENOSYS)}),
)
def test_publish_receipt_normalizes_unsupported_link_errno(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsupported_errno: int,
) -> None:
    destination = tmp_path / "receipt.json"

    def unsupported_link(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target, src_dir_fd, dst_dir_fd, follow_symlinks
        raise OSError(unsupported_errno, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module.os, "link", unsupported_link)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED
    assert "SENSITIVE_SENTINEL" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_publish_receipt_normalizes_missing_dir_fd_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"
    real_stat = os.stat

    def unsupported_stat(
        path: os.PathLike[str] | str,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if dir_fd is not None:
            raise NotImplementedError
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(cli_module.os, "stat", unsupported_stat)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED
    assert list(tmp_path.iterdir()) == []


def test_cli_maps_unsupported_publication_to_exit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = tmp_path / "receipt.json"

    def unsupported_link(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target, src_dir_fd, dst_dir_fd, follow_symlinks
        raise OSError(errno.ENOSYS, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(cli_module.os, "link", unsupported_link)

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 1
    assert not result.exists()
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED.value}\n"


def test_publish_receipt_allows_exactly_one_concurrent_writer(tmp_path: Path) -> None:
    destination = tmp_path / "receipt.json"
    barrier = Barrier(2)

    def attempt(payload: bytes) -> tuple[str, bytes]:
        barrier.wait()
        try:
            publish_receipt_no_clobber(destination, payload)
        except EvaluationValidationError as error:
            return error.code.value, payload
        return "SUCCESS", payload

    payloads = (b'{"writer":1}', b'{"writer":2}')
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(attempt, payloads))

    assert sorted(code for code, _ in outcomes) == [EvaluationErrorCode.RESULT_PATH_CONFLICT.value, "SUCCESS"]
    successful_payload = next(payload for code, payload in outcomes if code == "SUCCESS")
    assert destination.read_bytes() == successful_payload
    assert destination.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["receipt.json"]


@pytest.mark.parametrize(
    "config_name",
    [
        "dev-foundation-knowledge-retrieval-v1.execution.json",
        "dev-foundation-answer-grounding-safety-v1.execution.json",
        "dev-foundation-end-to-end-rag-v1.execution.json",
    ],
)
def test_run_dev_publishes_schema_valid_bundle(config_name: str, tmp_path: Path) -> None:
    run_id = str(uuid4())

    exit_code = main(
        [
            "run-dev",
            "--config",
            f"evals/configs/{config_name}",
            "--run-id",
            run_id,
            "--executed-by",
            "ceohwj",
        ],
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        adapter_registry=StaticRegistry(CountingAdapter()),
        clock=lambda: "2026-09-04T00:00:00.000000Z",
    )

    assert exit_code == 0
    result = tmp_path / run_id
    assert sorted(path.name for path in result.iterdir()) == [
        "cases.jsonl",
        "failures.jsonl",
        "metrics.json",
        "report.md",
        "result-content-manifest.json",
        "run.json",
        "suite-results.json",
    ]
    RagEvaluationRun.model_validate_json((result / "run.json").read_bytes())
    MetricResults.model_validate_json((result / "metrics.json").read_bytes())
    SuiteResults.model_validate_json((result / "suite-results.json").read_bytes())
    ContentManifest.model_validate_json((result / "result-content-manifest.json").read_bytes())


def test_retrieval_run_failure_timestamp_uses_controlled_run_clock(tmp_path: Path) -> None:
    timestamp = "2026-09-04T00:00:00.000000Z"
    run_id = str(uuid4())
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    exit_code = main(
        [
            "run-dev",
            "--config",
            "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
            "--run-id",
            run_id,
            "--executed-by",
            "ceohwj",
        ],
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        adapter_registry=build_adapter_registry(resolved),
        clock=FixedClock(timestamp),
    )

    assert exit_code == 0
    result = tmp_path / run_id
    run = json.loads((result / "run.json").read_bytes())
    failures = [json.loads(line) for line in (result / "failures.jsonl").read_bytes().splitlines()]
    assert len(failures) == 1
    assert failures[0]["created_at"] == timestamp
    assert run["started_at"] <= failures[0]["created_at"] <= run["completed_at"]


def test_run_dev_rejects_holdout_before_load_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    holdout = _manifest_payload(
        partition_counts={"AUTHORING": 0, "DEV": 0, "HOLDOUT": 1, "SAFETY_REGRESSION": 0},
        case_resources=[
            {
                "case_id": "holdout-001",
                "partition": "HOLDOUT",
                "path": "retrieval/cases/not-created.json",
                "sha256": "1" * 64,
            }
        ],
    )
    resolved = _resolved_for_manifest(tmp_path / "repository", holdout)
    called = False

    def spy_loader(*args: object, **kwargs: object) -> object:
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("load_dataset must not be called")

    monkeypatch.setattr(cli_module, "load_dev_execution_request", lambda *args, **kwargs: resolved, raising=False)
    monkeypatch.setattr(cli_module, "load_dataset", spy_loader)
    run_id = str(uuid4())

    exit_code = main(
        ["run-dev", "--config", "ignored.json", "--run-id", run_id, "--executed-by", "ceohwj"],
        allowed_result_root=tmp_path / "results",
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    assert exit_code == 2
    assert called is False
    assert not (tmp_path / "results" / run_id).exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.PARTITION_INVALID.value}\n"


def test_run_dev_passes_preflighted_manifest_snapshot_to_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    real_loader = cli_module.load_dataset
    observed_manifest_bytes: bytes | None = None

    def snapshot_loader(*args: Any, manifest_bytes: bytes | None = None, **kwargs: Any) -> Any:
        nonlocal observed_manifest_bytes
        observed_manifest_bytes = manifest_bytes
        return real_loader(*args, manifest_bytes=manifest_bytes, **kwargs)

    monkeypatch.setattr(cli_module, "load_dataset", snapshot_loader)
    run_id = str(uuid4())

    exit_code = main(
        [
            "run-dev",
            "--config",
            "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
            "--run-id",
            run_id,
            "--executed-by",
            "ceohwj",
        ],
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    assert exit_code == 0
    assert observed_manifest_bytes == resolved.dataset_manifest_bytes


def test_run_dev_rejects_dirty_repository_without_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = str(uuid4())

    exit_code = main(
        [
            "run-dev",
            "--config",
            "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
            "--run-id",
            run_id,
            "--executed-by",
            "ceohwj",
        ],
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, False),
    )

    assert exit_code == 2
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.REPOSITORY_STATE_INVALID.value}\n"


def test_run_dev_maps_loaded_binding_mismatch_without_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = str(uuid4())

    def reject_binding(*_args: Any, **_kwargs: Any) -> None:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    monkeypatch.setattr(cli_module, "validate_loaded_bindings", reject_binding, raising=False)
    exit_code = main(
        [
            "run-dev",
            "--config",
            "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
            "--run-id",
            run_id,
            "--executed-by",
            "ceohwj",
        ],
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    assert exit_code == 2
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.HASH_MISMATCH.value}\n"


def test_run_dev_is_semantically_stable_across_run_identity_and_clock(tmp_path: Path) -> None:
    run_ids = [str(uuid4()), str(uuid4())]
    times = ["2026-09-04T00:00:00.000000Z", "2026-09-04T00:01:00.000000Z"]
    for run_id, timestamp in zip(run_ids, times, strict=True):
        exit_code = main(
            [
                "run-dev",
                "--config",
                "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
                "--run-id",
                run_id,
                "--executed-by",
                "ceohwj",
            ],
            allowed_result_root=tmp_path,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
            adapter_registry=StaticRegistry(CountingAdapter()),
            clock=FixedClock(timestamp),
        )
        assert exit_code == 0

    def machine_files(run_id: str) -> dict[str, bytes]:
        result = tmp_path / run_id
        return {
            path.name: path.read_bytes()
            for path in result.iterdir()
            if path.name in {"run.json", "cases.jsonl", "metrics.json", "suite-results.json", "failures.jsonl"}
        }

    assert semantic_content_hash(machine_files(run_ids[0])) == semantic_content_hash(machine_files(run_ids[1]))


def _run_retrieval_cli(
    tmp_path: Path,
    config_name: str,
    run_id: str,
    *,
    baseline_run_id: str | None = None,
) -> int:
    arguments = [
        "run-dev",
        "--config",
        f"evals/configs/{config_name}",
        "--run-id",
        run_id,
        "--executed-by",
        "ceohwj",
    ]
    if baseline_run_id is not None:
        arguments.extend(["--baseline-run-id", baseline_run_id])
    return main(
        arguments,
        allowed_result_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        clock=FixedClock("2026-09-04T00:00:00.000000Z"),
    )


def test_run_dev_with_baseline_writes_comparison_into_candidate_bundle(tmp_path: Path) -> None:
    baseline_run_id = str(uuid4())
    candidate_run_id = str(uuid4())

    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-l-v1.execution.json",
            baseline_run_id,
        )
        == 0
    )
    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-hr-v1.execution.json",
            candidate_run_id,
            baseline_run_id=baseline_run_id,
        )
        == 0
    )

    comparison = json.loads((tmp_path / candidate_run_id / "comparison.json").read_bytes())
    assert comparison["baseline_run_id"] == baseline_run_id
    assert comparison["candidate_run_id"] == candidate_run_id
    assert comparison["decision_status"] == "INCONCLUSIVE"


@pytest.mark.parametrize(
    "candidate_config",
    [
        "rag-retrieval-dev-ret-l-v1.execution.json",
        "dev-foundation-answer-grounding-safety-v1.execution.json",
    ],
)
def test_run_dev_rejects_invalid_baseline_candidate_state(
    tmp_path: Path,
    candidate_config: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_run_id = str(uuid4())
    candidate_run_id = str(uuid4())
    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-l-v1.execution.json",
            baseline_run_id,
        )
        == 0
    )

    exit_code = _run_retrieval_cli(
        tmp_path,
        candidate_config,
        candidate_run_id,
        baseline_run_id=baseline_run_id,
    )

    assert exit_code == 2
    assert not (tmp_path / candidate_run_id).exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.STATE_COMBINATION_INVALID.value}\n"


def test_candidate_comparison_rejects_identical_retrieval_variant_manifest_hash(
    tmp_path: Path,
) -> None:
    baseline_run_id = str(uuid4())
    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-l-v1.execution.json",
            baseline_run_id,
        )
        == 0
    )
    baseline = cli_module.load_published_run_bundle(tmp_path, baseline_run_id)
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    relabeled = replace(
        resolved,
        request=resolved.request.model_copy(update={"variant_id": "RET-ALIAS"}),
    )

    with pytest.raises(EvaluationValidationError) as caught:
        cli_module._validate_baseline_candidate_state(baseline, relabeled)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_non_retrieval_baseline_option_rejects_state_before_missing_baseline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_run_id = str(uuid4())

    exit_code = _run_retrieval_cli(
        tmp_path,
        "dev-foundation-answer-grounding-safety-v1.execution.json",
        candidate_run_id,
        baseline_run_id=str(uuid4()),
    )

    assert exit_code == 2
    assert not (tmp_path / candidate_run_id).exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.STATE_COMBINATION_INVALID.value}\n"


def test_verify_result_prints_only_semantic_hash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_id = str(uuid4())
    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-l-v1.execution.json",
            run_id,
        )
        == 0
    )

    exit_code = main(["verify-result", "--run-id", run_id], allowed_result_root=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "5062fc278acefada2a5afc027867c324bb03ab62aaf934f964e692b9ad128b87\n"


def test_verify_result_accepts_valid_clock_rewrite_outside_semantic_hash(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = str(uuid4())
    assert (
        _run_retrieval_cli(
            tmp_path,
            "rag-retrieval-dev-ret-l-v1.execution.json",
            run_id,
        )
        == 0
    )
    run_path = tmp_path / run_id / "run.json"
    run = json.loads(run_path.read_bytes())
    run["started_at"] = "2026-09-04T01:00:00.000000Z"
    run["completed_at"] = "2026-09-04T01:01:00.000000Z"
    run_path.write_bytes(canonical_json_bytes(run))

    exit_code = main(["verify-result", "--run-id", run_id], allowed_result_root=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "5062fc278acefada2a5afc027867c324bb03ab62aaf934f964e692b9ad128b87\n"


@pytest.mark.parametrize("invalid_kind", ["missing", "symlink", "tampered"])
def test_verify_result_rejects_invalid_bundle_without_payload_output(
    tmp_path: Path,
    invalid_kind: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = str(uuid4())
    if invalid_kind != "missing":
        assert (
            _run_retrieval_cli(
                tmp_path,
                "rag-retrieval-dev-ret-l-v1.execution.json",
                run_id,
            )
            == 0
        )
    if invalid_kind == "symlink":
        target = tmp_path / run_id
        linked_run_id = str(uuid4())
        (tmp_path / linked_run_id).symlink_to(target, target_is_directory=True)
        run_id = linked_run_id
    elif invalid_kind == "tampered":
        (tmp_path / run_id / "metrics.json").write_bytes(b"SENSITIVE_SENTINEL")

    exit_code = main(["verify-result", "--run-id", run_id], allowed_result_root=tmp_path)

    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert captured.err == f"{EvaluationErrorCode.BASELINE_ARTIFACT_INVALID.value}\n"
    assert "SENSITIVE_SENTINEL" not in captured.err
