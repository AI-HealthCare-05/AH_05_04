"""Official-document MFDS endpoint candidates awaiting Local verification."""

from dataclasses import dataclass

from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    EmptyResultPolicy,
    EndpointContract,
    PaginationContract,
    ProviderBodyCodeContract,
    RequiredParameter,
    SourceClientLimits,
)

_P0_BY_OPERATION = {operation.operation_code: operation for operation in P0_OPERATIONS}


@dataclass(frozen=True, slots=True)
class MfdsEndpointCandidate:
    """Official-document candidate; not a completed Endpoint Receipt."""

    contract: EndpointContract
    secret_parameter_name: str
    request_parameters: tuple[tuple[str, str | int], ...]


_COMMON_BODY_CODES = ProviderBodyCodeContract(
    success_codes=("00",),
    authentication_failure_codes=("20", "30", "31"),
    daily_limit_codes=("22",),
)


def _limits(
    *,
    total_timeout_seconds: float,
    max_pages: int,
) -> SourceClientLimits:
    return SourceClientLimits(
        connect_timeout_seconds=3,
        read_timeout_seconds=10,
        total_timeout_seconds=total_timeout_seconds,
        max_redirects=0,
        max_response_bytes=5_000_000,
        max_decompressed_bytes=10_000_000,
        max_pages=max_pages,
        max_retry_attempts=2,
    )


def _candidate(
    *,
    operation_code: str,
    path: str,
    secret_parameter_name: str,
    primary_key_fields: tuple[str, ...],
    external_version_field: str | None,
    total_timeout_seconds: float,
    max_pages: int,
) -> MfdsEndpointCandidate:
    return MfdsEndpointCandidate(
        contract=EndpointContract(
            identity=_P0_BY_OPERATION[operation_code],
            method="GET",
            scheme="https",
            host="apis.data.go.kr",
            path_template=path,
            required_parameters=(
                RequiredParameter(
                    name=secret_parameter_name,
                    type_name="string",
                    location="query",
                    sensitive=True,
                ),
                RequiredParameter(
                    name="type",
                    type_name="string",
                    location="query",
                ),
            ),
            allowed_content_types=("application/json",),
            body_success_code_path="header.resultCode",
            body_error_code_path="header.resultCode",
            pagination=PaginationContract(
                mode="PAGE_NUMBER",
                page_parameter="pageNo",
                page_size_parameter="numOfRows",
                page_base=1,
                page_size_limit=100,
                end_condition="TOTAL_COUNT_REACHED",
            ),
            primary_key_fields=primary_key_fields,
            external_version_field=external_version_field,
            body_codes=_COMMON_BODY_CODES,
            limits=_limits(
                total_timeout_seconds=total_timeout_seconds,
                max_pages=max_pages,
            ),
            empty_result_policy=EmptyResultPolicy.REJECT,
        ),
        secret_parameter_name=secret_parameter_name,
        request_parameters=(("type", "json"),),
    )


MFDS_ENDPOINT_CANDIDATES = {
    "LIST_APPROVED_PRODUCTS": _candidate(
        operation_code="LIST_APPROVED_PRODUCTS",
        path=("/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnInq07"),
        secret_parameter_name="serviceKey",
        primary_key_fields=("ITEM_SEQ",),
        external_version_field=None,
        total_timeout_seconds=600,
        max_pages=500,
    ),
    "LIST_INGREDIENT_CONTRAINDICATIONS": _candidate(
        operation_code="LIST_INGREDIENT_CONTRAINDICATIONS",
        path=("/1471000/DURIrdntInfoService03/getUsjntTabooInfoList02"),
        secret_parameter_name="serviceKey",
        primary_key_fields=(
            "INGR_CODE",
            "MIXTURE_INGR_CODE",
            "NOTIFICATION_DATE",
        ),
        external_version_field="NOTIFICATION_DATE",
        total_timeout_seconds=180,
        max_pages=50,
    ),
    "LIST_PATIENT_MEDICATION_GUIDES": _candidate(
        operation_code="LIST_PATIENT_MEDICATION_GUIDES",
        path=("/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList"),
        secret_parameter_name="ServiceKey",
        primary_key_fields=("itemSeq",),
        external_version_field="updateDe",
        total_timeout_seconds=240,
        max_pages=100,
    ),
}
