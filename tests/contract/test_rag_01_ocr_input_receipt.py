from copy import deepcopy
from pathlib import Path

from scripts.verify_rag_01_receipt import (
    calculate_receipt_hash,
    load_receipt,
    verify_receipt_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = PROJECT_ROOT / "docs" / "validation" / "rag" / "rag-01-ocr-input-contract-receipt.json"
HUMAN_RECEIPT_PATH = PROJECT_ROOT / "docs" / "validation" / "rag" / "rag-01-ocr-input-contract-receipt.md"
TRACEABILITY_PATH = PROJECT_ROOT / "docs" / "testing" / "post-mvp-1-contract-traceability.md"


def test_receipt_has_valid_canonical_hash() -> None:
    verified_digest = verify_receipt_hash(RECEIPT_PATH)

    assert len(verified_digest) == 64


def test_generated_at_does_not_change_canonical_hash() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    regenerated_receipt = deepcopy(receipt)
    regenerated_receipt["generated_at"] = "2099-01-01T00:00:00+09:00"

    assert calculate_receipt_hash(regenerated_receipt) == calculate_receipt_hash(receipt)


def test_meaningful_receipt_change_changes_canonical_hash() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    changed_receipt = deepcopy(receipt)
    changed_receipt["verification_status"] = "CHANGED_FOR_TEST"

    assert calculate_receipt_hash(changed_receipt) != calculate_receipt_hash(receipt)


def test_required_source_locations_are_machine_readable() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    source_locations = receipt["current_runtime_model"]["source_locations"]
    locations_by_path = {location["path"]: location for location in source_locations}

    required_paths = {
        "backend/app/services/prescriptions.py",
        "backend/app/repositories/prescription_repository.py",
        "backend/app/models/prescriptions.py",
        "backend/app/dtos/prescriptions.py",
        "backend/alembic/versions/169a1b2c3d4e_create_prescription_version_foundation.py",
        "backend/alembic/versions/169b2c3d4e5f_backfill_prescription_versions.py",
        "backend/alembic/versions/169c3d4e5f6a_cut_over_prescription_version_reads.py",
        "backend/alembic/versions/169d4e5f6a7b_harden_prescription_version_links.py",
        "tests/migration/test_prescription_version_backfill_migration.py",
    }

    assert required_paths <= locations_by_path.keys()

    for location in source_locations:
        assert (PROJECT_ROOT / location["path"]).is_file()

    for path in required_paths:
        location = locations_by_path[path]
        assert location["lines"]
        assert location["commit_sha"]
        assert location["evidence_type"]


def test_machine_and_human_receipts_have_same_source_mapping_evidence() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    human_receipt = HUMAN_RECEIPT_PATH.read_text(encoding="utf-8")
    source_locations = receipt["current_runtime_model"]["source_locations"]

    for location in source_locations:
        expected_row = (
            f"| `{location['path']}` | {location['lines']} | "
            f"`{location['commit_sha']}` | `{location['evidence_type']}` | "
            f"{location['meaning']} |"
        )

        assert expected_row in human_receipt


def test_version_migration_chain_has_source_and_postgresql_evidence() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    migration_verification = receipt["migration_verification"]

    assert migration_verification["revisions"] == [
        "169a1b2c3d4e",
        "169b2c3d4e5f",
        "169c3d4e5f6a",
        "169d4e5f6a7b",
    ]
    assert migration_verification["source_inspection"]["status"] == "PASS"
    assert migration_verification["postgresql_alembic_execution"]["status"] == "PASS"
    assert migration_verification["source_inspection"]["commit_sha"] == receipt["verified_git_commit_sha"]
    assert (
        migration_verification["postgresql_alembic_execution"]["verified_commit_sha"]
        == receipt["verified_git_commit_sha"]
    )


def test_rag_08_09_gate_links_receipt_hash_and_remaining_prerequisites() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    receipt_hash = receipt["receipt_hash"]["value"]
    traceability = TRACEABILITY_PATH.read_text(encoding="utf-8")

    assert receipt_hash in traceability
    assert receipt["downstream_gate"]["prescription_version_input_ready"] is True
    assert receipt["downstream_gate"]["rag_08_09_ready"] is False
    assert "PRESCRIPTION_VERSION_NOT_IMPLEMENTED" not in receipt["downstream_gate"]["remaining_blockers"]
    assert "BLOCKED_BY_RAG_08_PREREQUISITE" in traceability
    assert "BLOCKED_BY_RAG_09_PREREQUISITE" in traceability
    assert "#170" in traceability
    assert "#171" in traceability
