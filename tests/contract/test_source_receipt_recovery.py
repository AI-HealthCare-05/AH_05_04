import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.verify_rag_01_receipt import calculate_receipt_hash, load_receipt
from scripts.verify_source_contract_receipt import (
    RECEIPT_PATH,
    RECOVERY_GUIDANCE,
    TARGET_PATH,
    TRACEABILITY_PATH,
    verify_source_contract_receipt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def copied_project(tmp_path: Path) -> Path:
    for relative in (RECEIPT_PATH, TARGET_PATH, TRACEABILITY_PATH):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PROJECT_ROOT / relative).read_bytes())
    return tmp_path


def test_repository_source_receipt_hashes_match() -> None:
    verify_source_contract_receipt(PROJECT_ROOT)


@pytest.mark.parametrize("changed", [TARGET_PATH, RECEIPT_PATH, TRACEABILITY_PATH])
def test_mismatch_reports_values_and_order_without_writing(copied_project: Path, changed: Path) -> None:
    path = copied_project / changed
    if changed == RECEIPT_PATH:
        receipt = load_receipt(path)
        receipt["receipt_version"] = "changed-for-test"
        path.write_text(json.dumps(receipt), encoding="utf-8")
    elif changed == TRACEABILITY_PATH:
        receipt = load_receipt(copied_project / RECEIPT_PATH)
        path.write_text(path.read_text().replace(receipt["receipt_hash"]["value"], "0" * 64), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b"\n<!-- synthetic contract change -->\n")
    before = {p: (copied_project / p).read_bytes() for p in (TARGET_PATH, RECEIPT_PATH, TRACEABILITY_PATH)}
    with pytest.raises(ValueError) as failure:
        verify_source_contract_receipt(copied_project)
    message = str(failure.value)
    assert str(changed) in message
    assert "stored=" in message and "calculated=" in message
    assert RECOVERY_GUIDANCE in message
    assert {p: (copied_project / p).read_bytes() for p in before} == before


def test_documented_recovery_repairs_all_three_hashes(copied_project: Path) -> None:
    target = copied_project / TARGET_PATH
    target.write_bytes(target.read_bytes() + "\n<!-- 합성 변경 -->\r\n".encode())
    receipt_path = copied_project / RECEIPT_PATH
    receipt = load_receipt(receipt_path)
    original_receipt = load_receipt(receipt_path)
    with pytest.raises(ValueError, match="local_target.sha256"):
        verify_source_contract_receipt(copied_project)
    receipt["contract_authority"]["local_target"]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_hash.value"):
        verify_source_contract_receipt(copied_project)
    traceability = copied_project / TRACEABILITY_PATH
    trace_before = traceability.read_bytes()
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/verify_rag_01_receipt.py"), str(RECEIPT_PATH), "--write"],
        cwd=copied_project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert traceability.read_bytes() == trace_before
    rewritten = load_receipt(receipt_path)
    assert rewritten["contract_authority"] == receipt["contract_authority"]
    with pytest.raises(ValueError, match="Source canonical hash"):
        verify_source_contract_receipt(copied_project)
    digest = result.stdout.strip()
    traceability.write_text(
        trace_before.decode().replace(original_receipt["receipt_hash"]["value"], digest), encoding="utf-8"
    )
    assert verify_source_contract_receipt(copied_project) == calculate_receipt_hash(rewritten) == digest
    for key in original_receipt.keys() - {"contract_authority", "receipt_hash"}:
        assert rewritten[key] == original_receipt[key]


@pytest.mark.parametrize("field", ["algorithm", "canonicalization"])
def test_metadata_checks_are_preserved(copied_project: Path, field: str) -> None:
    path = copied_project / RECEIPT_PATH
    receipt = load_receipt(path)
    receipt["receipt_hash"][field] = "unsupported"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported Receipt"):
        verify_source_contract_receipt(copied_project)


def test_unrelated_or_duplicate_traceability_hash_cannot_pass(copied_project: Path) -> None:
    path = copied_project / TRACEABILITY_PATH
    original = path.read_text()
    path.write_text(original.replace("(../../tests/fixtures/rag/source_contract_receipt.json)", "(other.json)"))
    with pytest.raises(ValueError, match="Source canonical hash"):
        verify_source_contract_receipt(copied_project)
    path.write_text(
        original
        + "\n"
        + next(
            line for line in original.splitlines() if "(../../tests/fixtures/rag/source_contract_receipt.json)" in line
        )
    )
    with pytest.raises(ValueError, match="Source canonical hash"):
        verify_source_contract_receipt(copied_project)
