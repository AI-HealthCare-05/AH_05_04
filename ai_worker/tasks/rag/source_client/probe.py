"""Local-only MFDS endpoint probe."""

import argparse
import asyncio
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointExecutionStatus,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRequest,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import (
    MFDS_ENDPOINT_CANDIDATES,
)
from ai_worker.tasks.rag.source_client.mfds_client import MfdsSourceClient
from ai_worker.tasks.rag.source_client.receipts import (
    EndpointReceipt,
    SanitizedFixtureEvidence,
    fixture_sha256,
    write_endpoint_receipt,
)


@dataclass(frozen=True, slots=True)
class ProbeOperation:
    """Stable operation metadata that does not include endpoint guesses."""

    identity: SourceOperationIdentity
    official_document_url: str


OPERATIONS = {
    "LIST_APPROVED_PRODUCTS": ProbeOperation(
        identity=SourceOperationIdentity(
            source_code="MFDS_PRODUCT_APPROVAL",
            endpoint_code="MFDS_PRODUCT_APPROVAL_API",
            operation_code="LIST_APPROVED_PRODUCTS",
        ),
        official_document_url=("https://www.data.go.kr/data/15095677/openapi.do"),
    ),
    "LIST_INGREDIENT_CONTRAINDICATIONS": ProbeOperation(
        identity=SourceOperationIdentity(
            source_code="MFDS_DUR",
            endpoint_code="MFDS_DUR_INGREDIENT_API",
            operation_code="LIST_INGREDIENT_CONTRAINDICATIONS",
        ),
        official_document_url=("https://www.data.go.kr/data/15056780/openapi.do"),
    ),
    "LIST_PATIENT_MEDICATION_GUIDES": ProbeOperation(
        identity=SourceOperationIdentity(
            source_code="MFDS_PATIENT_MEDICATION_GUIDE",
            endpoint_code="MFDS_PATIENT_GUIDE_API",
            operation_code="LIST_PATIENT_MEDICATION_GUIDES",
        ),
        official_document_url=("https://www.data.go.kr/data/15075057/openapi.do"),
    ),
}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


_SUCCESS_FIXTURE_BY_OPERATION = {
    "LIST_APPROVED_PRODUCTS": "list_approved_products_success.json",
    "LIST_INGREDIENT_CONTRAINDICATIONS": ("list_ingredient_contraindications_success.json"),
    "LIST_PATIENT_MEDICATION_GUIDES": ("list_patient_medication_guides_success.json"),
}


def build_fixture_evidence(
    operation_code: str,
) -> tuple[SanitizedFixtureEvidence, ...]:
    fixture_directory = repository_root() / "tests" / "fixtures" / "rag" / "mfds"
    fixture_names = (
        _SUCCESS_FIXTURE_BY_OPERATION[operation_code],
        "synthetic_auth_failure.json",
        "synthetic_daily_limit.json",
        "synthetic_empty.json",
        "synthetic_schema_drift.json",
    )
    evidence: list[SanitizedFixtureEvidence] = []

    for fixture_name in fixture_names:
        fixture_path = fixture_directory / fixture_name

        if not fixture_path.is_file():
            continue

        evidence.append(
            SanitizedFixtureEvidence(
                scenario=fixture_path.stem.upper(),
                path=str(fixture_path.relative_to(repository_root())),
                sha256=fixture_sha256(fixture_path),
            )
        )

    return tuple(evidence)


def build_not_run_receipt(
    operation: ProbeOperation,
    *,
    git_sha: str,
) -> EndpointReceipt:
    """Create a receipt without inventing an endpoint contract."""

    return EndpointReceipt(
        receipt_version="1.1",
        execution_status=EndpointExecutionStatus.NOT_RUN,
        identity=operation.identity,
        official_document_url=operation.official_document_url,
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
        fixture_evidence=build_fixture_evidence(
            operation.identity.operation_code,
        ),
        parser_activation_allowed=False,
        blocking_code="BLOCKED_BY_ENDPOINT_RECEIPT",
        validated_at=None,
        live_validation_git_sha=None,
        regression_fixture_git_sha=git_sha,
    )


def build_live_receipt(
    *,
    operation_code: str,
    result: SourceRunResult,
    validated_at: str,
    git_sha: str,
) -> EndpointReceipt:
    """전체 수집의 성공 또는 실패 결과를 안전한 증빙으로 변환합니다."""

    candidate = MFDS_ENDPOINT_CANDIDATES[operation_code]
    contract = candidate.contract
    validation = result.primary_key_validation
    parser_activation_allowed = result.snapshot_candidate_allowed
    external_version_field = contract.external_version_field
    external_version_field_exists = (
        external_version_field in validation.observed_fields
        if validation is not None and external_version_field is not None
        else None
    )
    blocking_code = None

    if not parser_activation_allowed:
        blocking_code = (
            "BLOCKED_BY_UNSTABLE_PRIMARY_KEY"
            if validation is not None and not validation.passed
            else "BLOCKED_BY_SOURCE_RUN_FAILURE"
        )

    return EndpointReceipt(
        receipt_version="1.1",
        execution_status=(
            EndpointExecutionStatus.COMPLETED if parser_activation_allowed else EndpointExecutionStatus.FAILED
        ),
        identity=contract.identity,
        official_document_url=OPERATIONS[operation_code].official_document_url,
        official_document_checked_at="2026-09-05",
        verified_http_method=contract.method,
        verified_scheme=contract.scheme,
        verified_host=contract.host,
        verified_path_template=contract.path_template,
        required_parameters=contract.required_parameters,
        allowed_content_types=contract.allowed_content_types,
        encoding="UTF-8",
        body_success_code_path=contract.body_success_code_path,
        body_success_codes=contract.body_codes.success_codes,
        body_error_code_path=contract.body_error_code_path,
        authentication_failure_codes=(contract.body_codes.authentication_failure_codes),
        daily_limit_codes=contract.body_codes.daily_limit_codes,
        pagination=contract.pagination,
        source_run_status=result.status,
        validated_record_count=(validation.record_count if validation is not None else None),
        primary_key_fields=contract.primary_key_fields,
        primary_key_null_count=(validation.null_count if validation is not None else None),
        primary_key_duplicate_count=(validation.duplicate_count if validation is not None else None),
        whole_record_duplicate_count=(validation.whole_record_duplicate_count if validation is not None else None),
        failure_code=(result.failure.code if result.failure is not None else None),
        external_version_field=external_version_field,
        external_version_field_exists=external_version_field_exists,
        limits=contract.limits,
        retry_mapping={
            "authentication_failure": RetryDisposition.NOT_RETRYABLE,
            "daily_limit": RetryDisposition.RETRY_AT_RESET,
            "temporary_failure": RetryDisposition.BACKOFF,
        },
        fixture_evidence=build_fixture_evidence(operation_code),
        parser_activation_allowed=parser_activation_allowed,
        blocking_code=blocking_code,
        validated_at=validated_at,
        live_validation_git_sha=git_sha,
        regression_fixture_git_sha=git_sha,
    )


def write_not_run_receipt(
    *,
    operation_code: str,
    output_dir: Path,
    generated_at: str,
    git_sha: str,
) -> Path:
    operation = OPERATIONS[operation_code]
    receipt = build_not_run_receipt(
        operation,
        git_sha=git_sha,
    )
    receipt_path = output_dir / f"{operation_code}.json"

    write_endpoint_receipt(
        receipt_path,
        receipt,
        generated_at=generated_at,
    )

    return receipt_path


def current_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "UNAVAILABLE"

    git_sha = result.stdout.strip()
    return git_sha or "UNAVAILABLE"


def live_validation_requested(
    environment: Mapping[str, str],
) -> bool:
    return environment.get("RAG_MFDS_LIVE_VALIDATION") == "1"


def require_local_secret(
    environment: Mapping[str, str],
) -> str:
    secret = environment.get("RAG_MFDS_API_KEY", "").strip()

    if not secret:
        raise ValueError("Local MFDS secret is required.")

    return secret


def calculate_last_page_number(
    *,
    total_count: int,
    page_size: int,
    page_base: int,
) -> int:
    if total_count <= 0 or page_size <= 0:
        raise ValueError("Positive pagination values are required.")

    return page_base + ((total_count - 1) // page_size)


def _pagination_schema_drift(
    operation: SourceOperationIdentity,
) -> SourceRunResult:
    return SourceRunResult(
        operation=operation,
        status=SourceRunStatus.SCHEMA_DRIFT,
        pages=(),
        failure=SourceClientFailure(
            code=SourceFailureCode.SCHEMA_DRIFT,
            retry=RetryDisposition.NOT_RETRYABLE,
            safe_message="MFDS pagination boundary is inconsistent.",
        ),
    )


async def run_live_probe(
    *,
    operation_code: str,
    secret: str,
    full_scan: bool = False,
) -> SourceRunResult:
    """Call the first and last documented MFDS pages without storing raw data."""

    candidate = MFDS_ENDPOINT_CANDIDATES[operation_code]
    contract = candidate.contract
    request = SourceRequest(
        operation=contract.identity,
        parameters=dict(candidate.request_parameters),
    )

    async with httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        headers={
            "Accept": "application/json",
            "User-Agent": "oryak-mfds-local-probe/1.0",
        },
    ) as http_client:
        source_client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name=candidate.secret_parameter_name,
            secret_value=secret,
            decoder=decode_mfds_json,
            client=http_client,
        )

        if full_scan:
            return await source_client.fetch_all_pages(request)

        first_result = await source_client.fetch_one_page(
            request,
            page_number=contract.pagination.page_base,
        )

        if first_result.status is not SourceRunStatus.SUCCEEDED:
            return first_result

        first_page = first_result.pages[0]
        total_count = first_page.total_count
        page_size = contract.pagination.page_size_limit

        if total_count is None or total_count <= 0:
            return _pagination_schema_drift(contract.identity)

        expected_first_count = min(total_count, page_size)
        if len(first_page.records) != expected_first_count:
            return _pagination_schema_drift(contract.identity)

        last_page_number = calculate_last_page_number(
            total_count=total_count,
            page_size=page_size,
            page_base=contract.pagination.page_base,
        )

        if last_page_number == first_page.page_number:
            return first_result

        last_result = await source_client.fetch_one_page(
            request,
            page_number=last_page_number,
        )

        if last_result.status is not SourceRunStatus.SUCCEEDED:
            return last_result

        last_page = last_result.pages[0]
        expected_last_count = total_count - (last_page_number - contract.pagination.page_base) * page_size

        if last_page.total_count != total_count or len(last_page.records) != expected_last_count:
            return _pagination_schema_drift(contract.identity)

        return SourceRunResult(
            operation=contract.identity,
            status=SourceRunStatus.SUCCEEDED,
            pages=(first_page, last_page),
            failure=None,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Local-only MFDS endpoint probe.",
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=tuple(OPERATIONS),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help=("Validate every page and the configured primary key candidate."),
    )
    parser.add_argument(
        "--write-receipt",
        action="store_true",
        help=("Write sanitized full-scan evidence without provider records."),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    current_environment = os.environ if environment is None else environment

    if live_validation_requested(current_environment):
        if args.write_receipt and not args.full_scan:
            parser.error("--write-receipt requires --full-scan.")

        try:
            secret = require_local_secret(current_environment)
        except ValueError as error:
            parser.error(str(error))

        result = asyncio.run(
            run_live_probe(
                operation_code=args.operation,
                secret=secret,
                full_scan=args.full_scan,
            )
        )
        failure = result.failure
        pages = result.pages
        primary_key_validation = result.primary_key_validation
        observed_fields = sorted({field_name for page in pages for record in page.records for field_name in record})
        total_count = pages[0].total_count if pages else None
        receipt_path: Path | None = None

        if args.write_receipt:
            generated_at = datetime.now(UTC).isoformat(timespec="seconds")
            receipt = build_live_receipt(
                operation_code=args.operation,
                result=result,
                validated_at=generated_at,
                git_sha=current_git_sha(),
            )
            receipt_path = args.output_dir / f"{args.operation}.json"
            write_endpoint_receipt(
                receipt_path,
                receipt,
                generated_at=generated_at,
                forbidden_values=(secret,),
            )

        reported_pages = (
            [pages[0].page_number, pages[-1].page_number]
            if args.full_scan and len(pages) > 1
            else [page.page_number for page in pages]
        )
        print(
            json.dumps(
                {
                    "operation_code": args.operation,
                    "source_run_status": result.status,
                    "record_count": result.record_count,
                    "observed_fields": observed_fields,
                    "total_count": total_count,
                    "page_count": len(pages),
                    "sampled_pages": reported_pages,
                    "full_scan": args.full_scan,
                    "pagination_boundary_verified": (
                        result.status is SourceRunStatus.SUCCEEDED and total_count is not None
                    ),
                    "primary_key_validation_passed": (
                        primary_key_validation.passed if primary_key_validation is not None else False
                    ),
                    "primary_key_null_count": (
                        primary_key_validation.null_count if primary_key_validation is not None else None
                    ),
                    "primary_key_duplicate_count": (
                        primary_key_validation.duplicate_count if primary_key_validation is not None else None
                    ),
                    "primary_key_missing_fields": (
                        dict(primary_key_validation.missing_field_counts) if primary_key_validation is not None else {}
                    ),
                    "primary_key_candidate_stats": (
                        [
                            {
                                "field": field_name,
                                "null_count": null_count,
                                "duplicate_count": duplicate_count,
                            }
                            for (
                                field_name,
                                null_count,
                                duplicate_count,
                            ) in primary_key_validation.candidate_field_stats
                        ]
                        if primary_key_validation is not None
                        else []
                    ),
                    "whole_record_duplicate_count": (
                        primary_key_validation.whole_record_duplicate_count
                        if primary_key_validation is not None
                        else None
                    ),
                    "failure_code": (failure.code if failure is not None else None),
                    "failure_reason": (failure.safe_message if failure is not None else None),
                    "retry": (failure.retry if failure is not None else None),
                    "http_status": (failure.http_status if failure is not None else 200),
                    "receipt_written": receipt_path is not None,
                    "receipt_path": (str(receipt_path) if receipt_path is not None else None),
                },
                ensure_ascii=False,
            )
        )

        return 0 if result.status is SourceRunStatus.SUCCEEDED else 1

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    receipt_path = write_not_run_receipt(
        operation_code=args.operation,
        output_dir=args.output_dir,
        generated_at=generated_at,
        git_sha=current_git_sha(),
    )

    print(
        json.dumps(
            {
                "operation_code": args.operation,
                "execution_status": EndpointExecutionStatus.NOT_RUN,
                "receipt_path": str(receipt_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
