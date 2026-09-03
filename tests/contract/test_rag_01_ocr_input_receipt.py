from copy import deepcopy
from pathlib import Path

from scripts.verify_rag_01_receipt import (
    calculate_receipt_hash,
    load_receipt,
    verify_receipt_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = PROJECT_ROOT / "docs" / "validation" / "rag" / "rag-01-ocr-input-contract-receipt.json"
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
        "backend/app/apis/v1/medical_document_routers.py",
        "backend/alembic/versions/529b2a36b677_add_medication_strength_and_ocr_prompt_.py",
        "backend/app/tests/ocr/test_prescription_confirmation_api.py",
        "backend/app/tests/ocr/test_prescription_confirmation_concurrency.py",
        "backend/app/tests/ocr/test_prescription_confirmation_validation.py",
    }

    assert required_paths <= locations_by_path.keys()

    for path in required_paths:
        location = locations_by_path[path]
        assert location["lines"]
        assert location["commit_sha"]
        assert location["evidence_type"]


def test_migration_source_and_postgresql_execution_are_distinguished() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    migration_verification = receipt["migration_verification"]

    assert migration_verification["revision"] == "529b2a36b677"
    assert migration_verification["source_inspection"]["status"] == "PASS"
    assert migration_verification["postgresql_alembic_execution"]["status"] == "PASS"
    assert (
        migration_verification["source_inspection"]["commit_sha"]
        != migration_verification["postgresql_alembic_execution"]["verified_commit_sha"]
    )


def test_rag_08_09_gate_links_receipt_hash_and_gap_code() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    receipt_hash = receipt["receipt_hash"]["value"]
    traceability = TRACEABILITY_PATH.read_text(encoding="utf-8")

    assert receipt_hash in traceability
    assert "PRESCRIPTION_VERSION_NOT_IMPLEMENTED" in traceability
    assert "#170" in traceability
    assert "#171" in traceability
