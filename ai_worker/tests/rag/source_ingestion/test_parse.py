from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    PrimaryKeyValidationResult,
    ProviderPage,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.probe import build_live_receipt
from ai_worker.tasks.rag.source_client.receipts import EndpointReceipt
from ai_worker.tasks.rag.source_ingestion.parse import (
    PRODUCT_CANONICALIZATION_SPEC_VERSION,
    calculate_product_run_checksum,
    calculate_verified_product_run_checksum,
    collect_parser_records,
    collect_verified_product_records,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PRODUCT_RECEIPT_PATH = REPOSITORY_ROOT / "docs" / "validation" / "rag" / "endpoints" / "LIST_APPROVED_PRODUCTS.json"


@pytest.fixture
def complete_run() -> SourceRunResult:
    return SourceRunResult(
        operation=P0_OPERATIONS[0],
        status=SourceRunStatus.SUCCEEDED,
        pages=(
            ProviderPage(
                page_number=1,
                records=(
                    {
                        "ITEM_SEQ": "synthetic-product-001",
                        "ITEM_NAME": "합성 의약품 1",
                        "synthetic_metadata": {"tags": ["original"]},
                    },
                ),
                response_checksum="a" * 64,
                content_type="application/json",
                total_count=2,
            ),
            ProviderPage(
                page_number=2,
                records=(
                    {
                        "ITEM_SEQ": "synthetic-product-002",
                        "ITEM_NAME": "합성 의약품 2",
                    },
                ),
                response_checksum="b" * 64,
                content_type="application/json",
                total_count=2,
            ),
        ),
        failure=None,
        primary_key_validation=PrimaryKeyValidationResult(
            passed=True,
            record_count=2,
            null_count=0,
            duplicate_count=0,
        ),
        full_scan_completed=True,
    )


@pytest.fixture
def receipt(complete_run: SourceRunResult) -> EndpointReceipt:
    # 외부 호출 없이 테스트용 Receipt 객체만 만듭니다.
    return build_live_receipt(
        operation_code="LIST_APPROVED_PRODUCTS",
        result=complete_run,
        validated_at="2026-09-07T00:00:00+00:00",
        git_sha="synthetic-test-only",
    )


def test_collects_records_from_all_pages(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    records = collect_parser_records(
        result=complete_run,
        receipt=receipt,
    )

    assert [record["ITEM_SEQ"] for record in records] == [
        "synthetic-product-001",
        "synthetic-product-002",
    ]
    assert len(records) == complete_run.record_count


def test_result_changes_do_not_modify_source_records(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    records = collect_parser_records(
        result=complete_run,
        receipt=receipt,
    )

    records[0]["ITEM_NAME"] = "변경된 이름"

    metadata = records[0]["synthetic_metadata"]
    assert isinstance(metadata, dict)
    tags = metadata["tags"]
    assert isinstance(tags, list)
    tags.append("changed")

    original = complete_run.pages[0].records[0]
    assert original["ITEM_NAME"] == "합성 의약품 1"
    assert original["synthetic_metadata"] == {"tags": ["original"]}


def test_rejects_incomplete_collection(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    incomplete_run = replace(
        complete_run,
        full_scan_completed=False,
    )

    with pytest.raises(ValueError, match="not eligible"):
        collect_parser_records(
            result=incomplete_run,
            receipt=receipt,
        )


def test_rejects_receipt_with_parser_disabled(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    blocked_receipt = replace(
        receipt,
        parser_activation_allowed=False,
    )

    with pytest.raises(ValueError, match="does not permit"):
        collect_parser_records(
            result=complete_run,
            receipt=blocked_receipt,
        )


def test_product_checksum_is_independent_of_page_layout(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    original_checksum = calculate_product_run_checksum(
        result=complete_run,
        receipt=receipt,
    )

    # 같은 레코드를 역순으로 모아 하나의 페이지로 재구성합니다.
    repaged_run = replace(
        complete_run,
        pages=(
            ProviderPage(
                page_number=1,
                records=(
                    complete_run.pages[1].records[0],
                    complete_run.pages[0].records[0],
                ),
                response_checksum="c" * 64,
                content_type="application/json",
                total_count=2,
            ),
        ),
    )

    assert (
        calculate_product_run_checksum(
            result=repaged_run,
            receipt=receipt,
        )
        == original_checksum
    )


@pytest.mark.parametrize(
    "primary_key_fields",
    [
        (),
        ("itemSeq",),
        ("ITEM_SEQ", "ITEM_NAME"),
    ],
)
def test_product_checksum_rejects_other_receipt_primary_keys(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
    primary_key_fields: tuple[str, ...],
) -> None:
    changed_receipt = replace(
        receipt,
        primary_key_fields=primary_key_fields,
    )

    with pytest.raises(ValueError, match="primary key"):
        calculate_product_run_checksum(
            result=complete_run,
            receipt=changed_receipt,
        )


@pytest.mark.parametrize("operation", P0_OPERATIONS[1:])
def test_product_checksum_rejects_other_operations(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
    operation: SourceOperationIdentity,
) -> None:
    # Receipt와 Run의 identity가 서로 일치해도 제품 Operation이 아니면 거부합니다.
    changed_run = replace(complete_run, operation=operation)
    changed_receipt = replace(receipt, identity=operation)

    with pytest.raises(ValueError, match="Only the product"):
        calculate_product_run_checksum(
            result=changed_run,
            receipt=changed_receipt,
        )


def test_product_checksum_rejects_duplicates_across_pages(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    duplicated_run = replace(
        complete_run,
        pages=(
            complete_run.pages[0],
            replace(
                complete_run.pages[1],
                records=complete_run.pages[0].records,
            ),
        ),
    )

    # 이전 검증 결과가 passed여도 현재 레코드의 중복을 다시 잡아냅니다.
    with pytest.raises(ValueError, match="valid ITEM_SEQ identifiers"):
        calculate_product_run_checksum(
            result=duplicated_run,
            receipt=receipt,
        )


def test_product_checksum_rejects_incomplete_run(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    with pytest.raises(ValueError, match="not eligible"):
        calculate_product_run_checksum(
            result=replace(complete_run, full_scan_completed=False),
            receipt=receipt,
        )


def test_product_checksum_rejects_disabled_receipt(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    with pytest.raises(ValueError, match="does not permit"):
        calculate_product_run_checksum(
            result=complete_run,
            receipt=replace(receipt, parser_activation_allowed=False),
        )


def test_collects_records_with_verified_product_receipt(
    complete_run: SourceRunResult,
) -> None:
    records = collect_verified_product_records(
        result=complete_run,
        receipt_path=PRODUCT_RECEIPT_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert [record["ITEM_SEQ"] for record in records] == [
        "synthetic-product-001",
        "synthetic-product-002",
    ]


def test_verified_product_receipt_rejects_other_operation(
    complete_run: SourceRunResult,
) -> None:
    changed_run = replace(
        complete_run,
        operation=P0_OPERATIONS[1],
    )

    with pytest.raises(ValueError, match="identity does not match"):
        collect_verified_product_records(
            result=changed_run,
            receipt_path=PRODUCT_RECEIPT_PATH,
            repository_root=REPOSITORY_ROOT,
        )


def test_verified_product_receipt_requires_fixture_files(
    complete_run: SourceRunResult,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="fixture could not be read"):
        collect_verified_product_records(
            result=complete_run,
            receipt_path=PRODUCT_RECEIPT_PATH,
            repository_root=tmp_path,
        )


def test_calculates_checksum_with_verified_product_receipt(
    complete_run: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    verified_checksum = calculate_verified_product_run_checksum(
        result=complete_run,
        receipt_path=PRODUCT_RECEIPT_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert verified_checksum == calculate_product_run_checksum(
        result=complete_run,
        receipt=receipt,
    )


def test_product_canonicalization_spec_version_is_fixed() -> None:
    assert PRODUCT_CANONICALIZATION_SPEC_VERSION == "mfds-product-approval@1"
