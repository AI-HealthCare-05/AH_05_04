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
        primary_key_fields=(),
        primary_key_null_count=None,
        primary_key_duplicate_count=None,
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
