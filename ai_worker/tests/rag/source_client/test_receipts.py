import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointExecutionStatus,
    RetryDisposition,
    SourceOperationIdentity,
)
from ai_worker.tasks.rag.source_client.receipts import (
    EndpointReceipt,
    SanitizedFixtureEvidence,
    build_receipt_payload,
    fixture_sha256,
    write_endpoint_receipt,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ENDPOINT_RECEIPT_DIRECTORY = REPOSITORY_ROOT / "docs" / "validation" / "rag" / "endpoints"


def not_run_receipt() -> EndpointReceipt:
    return EndpointReceipt(
        receipt_version="1.0",
        execution_status=EndpointExecutionStatus.NOT_RUN,
        identity=SourceOperationIdentity(
            source_code="MFDS_PRODUCT_APPROVAL",
            endpoint_code="MFDS_PRODUCT_APPROVAL_API",
            operation_code="LIST_APPROVED_PRODUCTS",
        ),
        official_document_url=("https://www.data.go.kr/data/15095677/openapi.do"),
        official_document_checked_at="2026-09-04",
        verified_http_method=None,
        verified_scheme=None,
        verified_host=None,
        verified_path_template=None,
        required_parameters=(),
        allowed_content_types=(),
        encoding=None,
        body_success_code_path=None,
        body_success_codes=(),
        body_error_code_path=None,
        authentication_failure_codes=(),
        daily_limit_codes=(),
        pagination=None,
        source_run_status=None,
        validated_record_count=None,
        primary_key_fields=(),
        primary_key_null_count=None,
        primary_key_duplicate_count=None,
        whole_record_duplicate_count=None,
        failure_code=None,
        external_version_field=None,
        external_version_field_exists=None,
        limits=None,
        retry_mapping={
            "authentication_failure": RetryDisposition.NOT_RETRYABLE,
            "daily_limit": RetryDisposition.RETRY_AT_RESET,
            "temporary_failure": RetryDisposition.BACKOFF,
        },
        fixture_evidence=(
            SanitizedFixtureEvidence(
                scenario="SYNTHETIC_SUCCESS",
                path=("tests/fixtures/rag/mfds/synthetic_success_page_1.json"),
                sha256="a" * 64,
            ),
        ),
        parser_activation_allowed=False,
        blocking_code="BLOCKED_BY_ENDPOINT_RECEIPT",
        validated_at=None,
        git_sha="synthetic-git-sha",
    )


def test_generated_at_does_not_change_receipt_hash() -> None:
    first_payload = build_receipt_payload(
        not_run_receipt(),
        generated_at="2026-09-04T10:00:00+09:00",
    )
    second_payload = build_receipt_payload(
        not_run_receipt(),
        generated_at="2026-09-04T11:00:00+09:00",
    )

    assert first_payload["receipt_hash"] == second_payload["receipt_hash"]


def test_meaningful_change_changes_receipt_hash() -> None:
    original = build_receipt_payload(
        not_run_receipt(),
        generated_at="2026-09-04T10:00:00+09:00",
    )
    changed_receipt = replace(
        not_run_receipt(),
        git_sha="different-synthetic-git-sha",
    )
    changed = build_receipt_payload(
        changed_receipt,
        generated_at="2026-09-04T10:00:00+09:00",
    )

    assert original["receipt_hash"] != changed["receipt_hash"]


def test_sensitive_value_is_rejected() -> None:
    secret = "private-local-secret"

    with pytest.raises(ValueError, match="sensitive"):
        build_receipt_payload(
            not_run_receipt(),
            generated_at="2026-09-04T10:00:00+09:00",
            forbidden_values=(secret, "synthetic-git-sha"),
        )


def test_authenticated_url_is_rejected() -> None:
    unsafe_receipt = replace(
        not_run_receipt(),
        official_document_url=("https://example.invalid/openapi?serviceKey=secret"),
    )

    with pytest.raises(ValueError, match="query strings"):
        build_receipt_payload(
            unsafe_receipt,
            generated_at="2026-09-04T10:00:00+09:00",
        )


def test_receipt_is_written_as_sanitized_json(tmp_path: Path) -> None:
    receipt_path = tmp_path / "LIST_APPROVED_PRODUCTS.json"

    payload = write_endpoint_receipt(
        receipt_path,
        not_run_receipt(),
        generated_at="2026-09-04T10:00:00+09:00",
    )
    stored_payload = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert stored_payload == payload
    assert stored_payload["execution_status"] == "NOT_RUN"
    assert stored_payload["parser_activation_allowed"] is False
    assert "receipt_hash" in stored_payload


def test_fixture_sha256_is_deterministic(tmp_path: Path) -> None:
    fixture_path = tmp_path / "synthetic.json"
    fixture_path.write_bytes(b'{"synthetic":true}')

    first_hash = fixture_sha256(fixture_path)
    second_hash = fixture_sha256(fixture_path)

    assert first_hash == second_hash
    assert len(first_hash) == 64


@pytest.mark.parametrize(
    "receipt_name",
    (
        "LIST_APPROVED_PRODUCTS.json",
        "LIST_INGREDIENT_CONTRAINDICATIONS.json",
        "LIST_PATIENT_MEDICATION_GUIDES.json",
    ),
)
def test_checked_in_endpoint_receipt_hashes_are_valid(
    receipt_name: str,
) -> None:
    receipt_path = ENDPOINT_RECEIPT_DIRECTORY / receipt_name
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_hash = payload.pop("receipt_hash")
    payload.pop("generated_at")
    canonical_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert hashlib.sha256(canonical_bytes).hexdigest() == expected_hash


@pytest.mark.parametrize(
    "receipt_name",
    (
        "LIST_APPROVED_PRODUCTS.json",
        "LIST_INGREDIENT_CONTRAINDICATIONS.json",
        "LIST_PATIENT_MEDICATION_GUIDES.json",
    ),
)
def test_checked_in_endpoint_receipt_fixture_hashes_are_valid(
    receipt_name: str,
) -> None:
    payload = json.loads(
        (ENDPOINT_RECEIPT_DIRECTORY / receipt_name).read_text(
            encoding="utf-8",
        )
    )

    for evidence in payload["fixture_evidence"]:
        fixture_path = REPOSITORY_ROOT / evidence["path"]

        assert fixture_path.is_file()
        assert fixture_sha256(fixture_path) == evidence["sha256"]


def test_checked_in_endpoint_receipts_preserve_activation_boundary() -> None:
    product = json.loads((ENDPOINT_RECEIPT_DIRECTORY / "LIST_APPROVED_PRODUCTS.json").read_text(encoding="utf-8"))
    dur = json.loads(
        (ENDPOINT_RECEIPT_DIRECTORY / "LIST_INGREDIENT_CONTRAINDICATIONS.json").read_text(encoding="utf-8")
    )
    patient_guide = json.loads(
        (ENDPOINT_RECEIPT_DIRECTORY / "LIST_PATIENT_MEDICATION_GUIDES.json").read_text(encoding="utf-8")
    )

    assert product["execution_status"] == "COMPLETED"
    assert product["parser_activation_allowed"] is True
    assert product["primary_key_duplicate_count"] == 0

    assert dur["execution_status"] == "FAILED"
    assert dur["parser_activation_allowed"] is False
    assert dur["primary_key_duplicate_count"] == 469
    assert dur["whole_record_duplicate_count"] == 1

    assert patient_guide["execution_status"] == "FAILED"
    assert patient_guide["parser_activation_allowed"] is False
    assert patient_guide["primary_key_duplicate_count"] == 17


@pytest.mark.parametrize(
    "receipt_name",
    (
        "LIST_APPROVED_PRODUCTS.json",
        "LIST_INGREDIENT_CONTRAINDICATIONS.json",
        "LIST_PATIENT_MEDICATION_GUIDES.json",
    ),
)
def test_checked_in_endpoint_receipts_contain_no_secret_values(
    receipt_name: str,
) -> None:
    payload = json.loads(
        (ENDPOINT_RECEIPT_DIRECTORY / receipt_name).read_text(
            encoding="utf-8",
        )
    )

    for parameter in payload["required_parameters"]:
        assert set(parameter) == {
            "location",
            "name",
            "sensitive",
            "type_name",
        }

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "credential_value" not in serialized
    assert "secret_value" not in serialized
    assert "api_key_value" not in serialized
    assert "provider_raw_body" not in serialized
