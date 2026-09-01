from __future__ import annotations

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from ai_worker.tasks.evaluation import cli as cli_module
from ai_worker.tasks.evaluation.cli import main, publish_receipt_no_clobber
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FOUNDATION_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"


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
