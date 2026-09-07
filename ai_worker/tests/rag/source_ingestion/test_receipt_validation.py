import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    ReceiptFixtureEvidence,
    calculate_endpoint_receipt_hash,
    load_product_endpoint_receipt,
    verify_receipt_fixture_evidence,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PRODUCT_RECEIPT_PATH = REPOSITORY_ROOT / "docs" / "validation" / "rag" / "endpoints" / "LIST_APPROVED_PRODUCTS.json"


def _product_payload() -> dict[str, object]:
    decoded = json.loads(PRODUCT_RECEIPT_PATH.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def _write_payload(
    path: Path,
    payload: dict[str, object],
) -> None:
    payload["receipt_hash"] = calculate_endpoint_receipt_hash(payload)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_loads_verified_product_receipt() -> None:
    evidence = load_product_endpoint_receipt(PRODUCT_RECEIPT_PATH)

    assert evidence.receipt_version == "1.1"
    assert evidence.identity.operation_code == "LIST_APPROVED_PRODUCTS"
    assert evidence.validated_record_count == 42989
    assert len(evidence.receipt_hash) == 64


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload.update(execution_status="FAILED"),
        lambda payload: payload.update(source_run_status="SCHEMA_DRIFT"),
        lambda payload: payload.update(parser_activation_allowed=False),
        lambda payload: payload.update(blocking_code="BLOCKED_BY_ENDPOINT_RECEIPT"),
        lambda payload: payload.update(primary_key_fields=["itemSeq"]),
        lambda payload: payload.update(primary_key_null_count=1),
        lambda payload: payload.update(primary_key_duplicate_count=1),
        lambda payload: payload.update(validated_record_count=0),
    ],
)
def test_rejects_ineligible_product_receipt(
    tmp_path: Path,
    change: Callable[[dict[str, object]], None],
) -> None:
    payload = deepcopy(_product_payload())
    change(payload)

    path = tmp_path / "receipt.json"
    _write_payload(path, payload)

    with pytest.raises(ValueError, match="invalid"):
        load_product_endpoint_receipt(path)


def test_rejects_other_operation_identity(tmp_path: Path) -> None:
    payload = deepcopy(_product_payload())
    identity = payload["identity"]
    assert isinstance(identity, dict)
    identity["operation_code"] = "LIST_INGREDIENT_CONTRAINDICATIONS"

    path = tmp_path / "receipt.json"
    _write_payload(path, payload)

    with pytest.raises(ValueError, match="invalid identity"):
        load_product_endpoint_receipt(path)


def test_rejects_changed_payload_with_stale_hash(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_product_payload())
    payload["validated_record_count"] = 1

    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        load_product_endpoint_receipt(path)


def test_rejects_duplicate_json_key(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(
        '{"receipt_hash":"","receipt_hash":""}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        load_product_endpoint_receipt(path)


@pytest.mark.parametrize(
    "serialized",
    [
        "[]",
        "null",
        "{invalid-json",
    ],
)
def test_rejects_invalid_receipt_document(
    tmp_path: Path,
    serialized: str,
) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(serialized, encoding="utf-8")

    with pytest.raises(ValueError):
        load_product_endpoint_receipt(path)


def test_rejects_missing_file_without_exposing_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-receipt-name.json"

    with pytest.raises(ValueError) as captured:
        load_product_endpoint_receipt(path)

    assert str(captured.value) == ("Endpoint receipt could not be read.")
    assert str(path) not in str(captured.value)


def test_verifies_product_receipt_fixture_files() -> None:
    evidence = load_product_endpoint_receipt(PRODUCT_RECEIPT_PATH)

    verify_receipt_fixture_evidence(
        evidence=evidence.fixture_evidence,
        repository_root=REPOSITORY_ROOT,
    )

    assert len(evidence.fixture_evidence) == 5


def test_rejects_changed_fixture_content(tmp_path: Path) -> None:
    relative_path = "tests/fixtures/rag/mfds/changed.json"
    file_path = tmp_path / relative_path
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"changed")

    evidence = (
        ReceiptFixtureEvidence(
            scenario="SYNTHETIC_CHANGED",
            path=relative_path,
            sha256="a" * 64,
        ),
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_receipt_fixture_evidence(
            evidence=evidence,
            repository_root=tmp_path,
        )


def test_missing_fixture_error_does_not_expose_path(
    tmp_path: Path,
) -> None:
    evidence = (
        ReceiptFixtureEvidence(
            scenario="SYNTHETIC_MISSING",
            path="tests/fixtures/rag/mfds/private-name.json",
            sha256="a" * 64,
        ),
    )

    with pytest.raises(ValueError) as captured:
        verify_receipt_fixture_evidence(
            evidence=evidence,
            repository_root=tmp_path,
        )

    assert str(captured.value) == ("Endpoint receipt fixture could not be read.")
    assert "private-name.json" not in str(captured.value)


@pytest.mark.parametrize(
    "fixture_evidence",
    [
        [],
        [
            {
                "scenario": "SYNTHETIC_SUCCESS",
                "path": "../outside.json",
                "sha256": "a" * 64,
            }
        ],
        [
            {
                "scenario": "invalid scenario",
                "path": ("tests/fixtures/rag/mfds/synthetic.json"),
                "sha256": "a" * 64,
            }
        ],
        [
            {
                "scenario": "SYNTHETIC_SUCCESS",
                "path": ("tests/fixtures/rag/mfds/synthetic.json"),
                "sha256": "invalid",
            }
        ],
        [
            {
                "scenario": "SYNTHETIC_DUPLICATE",
                "path": "tests/fixtures/rag/mfds/a.json",
                "sha256": "a" * 64,
            },
            {
                "scenario": "SYNTHETIC_DUPLICATE",
                "path": "tests/fixtures/rag/mfds/b.json",
                "sha256": "b" * 64,
            },
        ],
    ],
)
def test_rejects_invalid_fixture_evidence(
    tmp_path: Path,
    fixture_evidence: list[object],
) -> None:
    payload = deepcopy(_product_payload())
    payload["fixture_evidence"] = fixture_evidence

    path = tmp_path / "receipt.json"
    _write_payload(path, payload)

    with pytest.raises(ValueError, match="fixture"):
        load_product_endpoint_receipt(path)


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload.update(verified_http_method="POST"),
        lambda payload: payload.update(verified_host="untrusted.example"),
        lambda payload: payload.update(verified_path_template="/other/path"),
        lambda payload: payload.update(required_parameters=[]),
        lambda payload: payload.update(body_error_code_path="body.errorCode"),
        lambda payload: payload.update(authentication_failure_codes=[]),
        lambda payload: payload.update(daily_limit_codes=[]),
        lambda payload: payload.update(pagination={}),
        lambda payload: payload.update(limits={}),
        lambda payload: payload.update(external_version_field="UPDATE_DATE"),
    ],
)
def test_rejects_receipt_that_differs_from_product_contract(
    tmp_path: Path,
    change: Callable[[dict[str, object]], None],
) -> None:
    payload = deepcopy(_product_payload())
    change(payload)

    path = tmp_path / "receipt.json"
    _write_payload(path, payload)

    with pytest.raises(ValueError, match="invalid"):
        load_product_endpoint_receipt(path)
