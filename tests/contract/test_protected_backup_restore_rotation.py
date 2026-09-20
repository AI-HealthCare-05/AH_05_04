"""Focused contracts for Issue #889 protected backup, restore, and rotation."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from asyncpg import InvalidPasswordError

from infra.python import protected_retrieval_backup_ops as ops

KEY = base64.b64encode(bytes(range(32))).decode("ascii")
OTHER_KEY = base64.b64encode(bytes(reversed(range(32)))).decode("ascii")


def test_streaming_aes_gcm_round_trip_and_fail_closed_authentication() -> None:
    plaintext = (b"protected synthetic fixture\n" * 10_000) + b"end"
    encrypted = io.BytesIO()

    ops.encrypt_stream(io.BytesIO(plaintext), encrypted, encoded_key=KEY, key_version="kv-2026-09")
    artifact = encrypted.getvalue()
    assert plaintext not in artifact

    restored = io.BytesIO()
    ops.decrypt_stream(io.BytesIO(artifact), restored, encoded_key=KEY, expected_key_version="kv-2026-09")
    assert restored.getvalue() == plaintext

    for encoded_key, payload in (
        (OTHER_KEY, artifact),
        (KEY, artifact[:-1] + bytes([artifact[-1] ^ 1])),
    ):
        with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_AUTHENTICATION_FAILED"):
            ops.decrypt_stream(
                io.BytesIO(payload),
                io.BytesIO(),
                encoded_key=encoded_key,
                expected_key_version="kv-2026-09",
            )

    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_ENCRYPTION_KEY_INVALID"):
        ops.encrypt_stream(io.BytesIO(b"x"), io.BytesIO(), encoded_key="c2hvcnQ=", key_version="kv")


def test_exact_scope_and_backup_identity_contract() -> None:
    ops.validate_protected_scope(set(ops.EXPECTED_RELATIONS), {"sha256_hex"})

    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_SCOPE_DRIFT"):
        ops.validate_protected_scope({*ops.EXPECTED_RELATIONS, "future_table"}, {"sha256_hex"})
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_SCOPE_MISSING"):
        ops.validate_protected_scope(set(ops.EXPECTED_RELATIONS) - {"audit_head"}, {"sha256_hex"})
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_SCOPE_DRIFT"):
        ops.validate_protected_scope(set(ops.EXPECTED_RELATIONS), {"sha256_hex"}, {"routine:future_export"})

    ops.validate_backup_identity_distinct("backup_login", ["admin", "data", "control", "app", "source"])
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_IDENTITY_COLLISION"):
        ops.validate_backup_identity_distinct("data", ["admin", "data", "control"])

    assert ops.BACKUP_ALLOWED_PRIVILEGES == frozenset({"SELECT"})
    assert set(ops.BACKUP_RELATIONS) == set(ops.EXPECTED_RELATIONS)


def test_public_receipts_have_deterministic_exact_allowlists() -> None:
    now = datetime(2026, 9, 20, 1, 2, 3, tzinfo=UTC)
    receipt = ops.build_backup_receipt(
        backup_id="prb-20260920T010203Z-abcdef123456",
        recorded_at=now,
        reason="EVIDENCE_REHEARSAL",
        scope_ref="contract-freeze-v4",
        encrypted_artifact_sha256="a" * 64,
        encryption_key_version="kv-2026-09",
        acl_policy_ref="acl-v1",
    )
    assert tuple(receipt) == ops.BACKUP_RECEIPT_FIELDS
    assert receipt["relation_count"] == 9
    assert json.loads(ops.serialize_public_receipt(receipt)) == receipt

    restore_receipt = ops.build_restore_receipt("prb-20260920T010203Z-abcdef123456")
    assert tuple(restore_receipt) == ops.RESTORE_RECEIPT_FIELDS
    assert json.loads(ops.serialize_public_receipt(restore_receipt)) == restore_receipt

    for forbidden in (
        {**receipt, "actual_path": "/protected-backups/x"},
        {**receipt, "nested": {"db_host": "postgres"}},
        {**receipt, "password": "secret"},
    ):
        with pytest.raises(ops.ProtectedBackupOpsError, match="RECEIPT_CONTRACT_VIOLATION"):
            ops.serialize_public_receipt(forbidden)


def test_restore_target_and_artifact_id_are_fail_closed() -> None:
    ops.validate_restore_target("protected-retrieval-restore-db")
    for host in ("postgres", "localhost", "127.0.0.1", "db.example.invalid"):
        with pytest.raises(ops.ProtectedBackupOpsError, match="RESTORE_TARGET_FORBIDDEN"):
            ops.validate_restore_target(host)

    assert ops.artifact_filename("prb-20260920T010203Z-abcdef123456") == ("prb-20260920T010203Z-abcdef123456.prb")
    for value in ("../backup", "/tmp/backup", "backup.prb", "", "A" * 100):
        with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_ID_INVALID"):
            ops.artifact_filename(value)


class _RotationDriver:
    def __init__(self, *, fail_apply: bool = False, fail_next: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_apply = fail_apply
        self.fail_next = fail_next

    def validate_current(self) -> None:
        self.calls.append("validate_current")

    def apply_next(self) -> None:
        self.calls.append("apply_next")
        if self.fail_apply:
            raise RuntimeError("partial mutation")

    def validate_next(self) -> None:
        self.calls.append("validate_next")
        if self.fail_next:
            raise RuntimeError("opaque failure")

    def validate_old_rejected(self) -> None:
        self.calls.append("validate_old_rejected")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def validate_rollback(self) -> None:
        self.calls.append("validate_rollback")


def test_rotation_success_and_failure_rollback_are_secret_free() -> None:
    driver = _RotationDriver()
    receipt = ops.rotate_database_credentials(driver, recorded_at=datetime(2026, 9, 20, 1, 2, 3, tzinfo=UTC))
    assert driver.calls == [
        "validate_current",
        "apply_next",
        "validate_next",
        "validate_old_rejected",
    ]
    assert tuple(receipt) == ops.ROTATION_RECEIPT_FIELDS
    assert all(value == "PASS" for key, value in receipt.items() if key not in {"operation", "recorded_at"})

    failing = _RotationDriver(fail_next=True)
    with pytest.raises(ops.ProtectedBackupOpsError, match="DB_ROTATION_FAILED") as exc_info:
        ops.rotate_database_credentials(failing)
    assert failing.calls[-2:] == ["rollback", "validate_rollback"]
    assert "opaque failure" not in str(exc_info.value)

    partial = _RotationDriver(fail_apply=True)
    with pytest.raises(ops.ProtectedBackupOpsError, match="DB_ROTATION_FAILED"):
        ops.rotate_database_credentials(partial)
    assert partial.calls[-2:] == ["rollback", "validate_rollback"]

    wrapped = RuntimeError("fixed wrapper")
    wrapped.__cause__ = InvalidPasswordError("redacted")
    assert ops._is_invalid_password_error(wrapped)
    assert not ops._is_invalid_password_error(ConnectionError("network unavailable"))


def test_storage_mapping_rejects_overwrite_symlink_and_unsafe_modes(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    backup_id = "prb-20260920T010203Z-abcdef123456"
    target = ops._artifact_path(tmp_path, backup_id, must_exist=False)
    target.write_bytes(b"encrypted-only")
    target.chmod(0o600)
    assert ops._artifact_path(tmp_path, backup_id, must_exist=True) == target
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_ARTIFACT_STATE_INVALID"):
        ops._artifact_path(tmp_path, backup_id, must_exist=False)

    target.unlink()
    target.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_ARTIFACT_STATE_INVALID"):
        ops._artifact_path(tmp_path, backup_id, must_exist=False)

    target.unlink()
    tmp_path.chmod(0o755)
    with pytest.raises(ops.ProtectedBackupOpsError, match="BACKUP_STORAGE_INVALID"):
        ops._artifact_path(tmp_path, backup_id, must_exist=False)


def test_audit_chain_mismatch_is_rejected() -> None:
    entries = [
        {
            "sequence": 1,
            "entry_body": {
                "event": "synthetic",
                "sequence": 1,
                "previous_entry_sha256": None,
                "entry_sha256": "",
            },
            "previous_entry_sha256": None,
            "entry_sha256": "",
        }
    ]
    entries[0]["entry_sha256"] = ops.audit_entry_sha256(entries[0]["entry_body"])
    entries[0]["entry_body"]["entry_sha256"] = entries[0]["entry_sha256"]
    ops.validate_audit_chain(entries, {"sequence": 1, "entry_sha256": entries[0]["entry_sha256"]})
    entries[0]["entry_sha256"] = "0" * 64
    with pytest.raises(ops.ProtectedBackupOpsError, match="RESTORE_AUDIT_INTEGRITY_FAILED"):
        ops.validate_audit_chain(entries, {"sequence": 1, "entry_sha256": "0" * 64})
