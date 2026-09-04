import json
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointExecutionStatus,
    ProviderPage,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.probe import (
    OPERATIONS,
    build_not_run_receipt,
    calculate_last_page_number,
    live_validation_requested,
    main,
    require_local_secret,
    write_not_run_receipt,
)


@pytest.mark.parametrize(
    ("total_count", "page_size", "expected"),
    (
        (1, 100, 1),
        (100, 100, 1),
        (101, 100, 2),
        (250, 100, 3),
    ),
)
def test_calculates_last_page_number(
    total_count: int,
    page_size: int,
    expected: int,
) -> None:
    assert (
        calculate_last_page_number(
            total_count=total_count,
            page_size=page_size,
            page_base=1,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("total_count", "page_size"),
    (
        (0, 100),
        (1, 0),
    ),
)
def test_rejects_invalid_pagination_values(
    total_count: int,
    page_size: int,
) -> None:
    with pytest.raises(ValueError, match="Positive pagination"):
        calculate_last_page_number(
            total_count=total_count,
            page_size=page_size,
            page_base=1,
        )


def test_all_three_p0_operations_are_registered() -> None:
    assert set(OPERATIONS) == {
        "LIST_APPROVED_PRODUCTS",
        "LIST_INGREDIENT_CONTRAINDICATIONS",
        "LIST_PATIENT_MEDICATION_GUIDES",
    }


@pytest.mark.parametrize(
    ("operation_code", "expected_source", "expected_endpoint"),
    (
        (
            "LIST_APPROVED_PRODUCTS",
            "MFDS_PRODUCT_APPROVAL",
            "MFDS_PRODUCT_APPROVAL_API",
        ),
        (
            "LIST_INGREDIENT_CONTRAINDICATIONS",
            "MFDS_DUR",
            "MFDS_DUR_INGREDIENT_API",
        ),
        (
            "LIST_PATIENT_MEDICATION_GUIDES",
            "MFDS_PATIENT_MEDICATION_GUIDE",
            "MFDS_PATIENT_GUIDE_API",
        ),
    ),
)
def test_not_run_receipt_uses_stable_operation_identity(
    operation_code: str,
    expected_source: str,
    expected_endpoint: str,
) -> None:
    receipt = build_not_run_receipt(
        OPERATIONS[operation_code],
        git_sha="synthetic-git-sha",
    )

    assert receipt.execution_status is EndpointExecutionStatus.NOT_RUN
    assert receipt.identity.source_code == expected_source
    assert receipt.identity.endpoint_code == expected_endpoint
    assert receipt.identity.operation_code == operation_code
    assert receipt.parser_activation_allowed is False
    assert receipt.blocking_code == "BLOCKED_BY_ENDPOINT_RECEIPT"


def test_live_validation_requires_exact_opt_in() -> None:
    assert live_validation_requested({}) is False
    assert live_validation_requested({"RAG_MFDS_LIVE_VALIDATION": "0"}) is False
    assert live_validation_requested({"RAG_MFDS_LIVE_VALIDATION": "true"}) is False
    assert live_validation_requested({"RAG_MFDS_LIVE_VALIDATION": "1"}) is True


def test_local_secret_is_required_for_live_validation() -> None:
    with pytest.raises(ValueError, match="secret"):
        require_local_secret({})

    with pytest.raises(ValueError, match="secret"):
        require_local_secret({"RAG_MFDS_API_KEY": "   "})

    assert require_local_secret({"RAG_MFDS_API_KEY": "synthetic-local-secret"}) == "synthetic-local-secret"


def test_default_probe_writes_not_run_without_secret(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "--operation",
            "LIST_APPROVED_PRODUCTS",
            "--output-dir",
            str(tmp_path),
        ],
        environment={},
    )

    receipt_path = tmp_path / "LIST_APPROVED_PRODUCTS.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    command_output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["execution_status"] == "NOT_RUN"
    assert payload["verified_host"] is None
    assert payload["verified_path_template"] is None
    assert payload["parser_activation_allowed"] is False
    assert command_output["execution_status"] == "NOT_RUN"


def test_not_run_receipt_contains_fixture_hashes(tmp_path: Path) -> None:
    receipt_path = write_not_run_receipt(
        operation_code="LIST_APPROVED_PRODUCTS",
        output_dir=tmp_path,
        generated_at="2026-09-04T10:00:00+00:00",
        git_sha="synthetic-git-sha",
    )
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert payload["fixture_evidence"]
    assert all(len(evidence["sha256"]) == 64 for evidence in payload["fixture_evidence"])


def test_live_probe_without_secret_fails_before_writing(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--operation",
                "LIST_APPROVED_PRODUCTS",
                "--output-dir",
                str(tmp_path),
            ],
            environment={"RAG_MFDS_LIVE_VALIDATION": "1"},
        )

    assert exc_info.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_live_probe_calls_registered_endpoint_without_writing_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | bool] = {}

    async def fake_run_live_probe(
        *,
        operation_code: str,
        secret: str,
        full_scan: bool = False,
    ) -> SourceRunResult:
        captured["operation_code"] = operation_code
        captured["secret"] = secret
        captured["full_scan"] = full_scan

        operation = OPERATIONS[operation_code].identity
        return SourceRunResult(
            operation=operation,
            status=SourceRunStatus.SUCCEEDED,
            pages=(
                ProviderPage(
                    page_number=1,
                    records=({"ITEM_SEQ": "synthetic-product-001"},),
                    response_checksum="a" * 64,
                    content_type="application/json",
                    total_count=1,
                ),
            ),
            failure=None,
        )

    monkeypatch.setattr(
        "ai_worker.tasks.rag.source_client.probe.run_live_probe",
        fake_run_live_probe,
    )

    exit_code = main(
        [
            "--operation",
            "LIST_APPROVED_PRODUCTS",
            "--output-dir",
            str(tmp_path),
        ],
        environment={
            "RAG_MFDS_LIVE_VALIDATION": "1",
            "RAG_MFDS_API_KEY": "synthetic-local-secret",
        },
    )

    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert captured == {
        "operation_code": "LIST_APPROVED_PRODUCTS",
        "secret": "synthetic-local-secret",
        "full_scan": False,
    }
    assert payload["source_run_status"] == "SUCCEEDED"
    assert payload["record_count"] == 1
    assert payload["total_count"] == 1
    assert payload["sampled_pages"] == [1]
    assert payload["pagination_boundary_verified"] is True
    assert payload["receipt_written"] is False
    assert "synthetic-local-secret" not in output
    assert list(tmp_path.iterdir()) == []
    assert payload["page_count"] == 1
    assert payload["full_scan"] is False
    assert payload["primary_key_validation_passed"] is False
