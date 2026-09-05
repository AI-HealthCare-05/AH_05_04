"""Sanitized endpoint receipt serialization."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointExecutionStatus,
    PaginationContract,
    RequiredParameter,
    RetryDisposition,
    SourceClientLimits,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRunStatus,
)


@dataclass(frozen=True, slots=True)
class SanitizedFixtureEvidence:
    """Checksum evidence for a minimal non-sensitive fixture."""

    scenario: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class EndpointReceipt:
    """Sanitized verification evidence for one MFDS operation."""

    receipt_version: str
    execution_status: EndpointExecutionStatus
    identity: SourceOperationIdentity
    official_document_url: str
    official_document_checked_at: str
    verified_http_method: str | None
    verified_scheme: str | None
    verified_host: str | None
    verified_path_template: str | None
    required_parameters: tuple[RequiredParameter, ...]
    allowed_content_types: tuple[str, ...]
    encoding: str | None
    body_success_code_path: str | None
    body_success_codes: tuple[str, ...]
    body_error_code_path: str | None
    authentication_failure_codes: tuple[str, ...]
    daily_limit_codes: tuple[str, ...]
    pagination: PaginationContract | None
    source_run_status: SourceRunStatus | None
    validated_record_count: int | None
    primary_key_fields: tuple[str, ...]
    primary_key_null_count: int | None
    primary_key_duplicate_count: int | None
    whole_record_duplicate_count: int | None
    failure_code: SourceFailureCode | None
    external_version_field: str | None
    external_version_field_exists: bool | None
    limits: SourceClientLimits | None
    retry_mapping: Mapping[str, RetryDisposition]
    fixture_evidence: tuple[SanitizedFixtureEvidence, ...]
    parser_activation_allowed: bool
    blocking_code: str | None
    validated_at: str | None
    live_validation_git_sha: str | None
    regression_fixture_git_sha: str


_FORBIDDEN_FIELD_NAMES = {
    "authorization_header",
    "credential_value",
    "secret_value",
    "api_key_value",
    "provider_raw_body",
    "raw_provider_body",
    "authenticated_url",
    "query_string",
}


def fixture_sha256(path: Path) -> str:
    """Calculate a fixture hash without reading it into a receipt."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_receipt_payload(
    receipt: EndpointReceipt,
    *,
    generated_at: str,
    forbidden_values: Sequence[str] = (),
) -> dict[str, object]:
    """Build a sanitized payload with a deterministic receipt hash."""

    raw_payload: dict[str, object] = asdict(receipt)
    raw_payload["generated_at"] = generated_at

    # tuple·StrEnum 등을 실제 JSON 저장 결과와 같은 자료형으로
    # 정규화합니다.
    payload: dict[str, object] = json.loads(
        json.dumps(
            raw_payload,
            ensure_ascii=False,
        )
    )

    _validate_sanitized_value(payload)
    _reject_forbidden_values(payload, forbidden_values)

    canonical_payload = {key: value for key, value in payload.items() if key not in {"generated_at", "receipt_hash"}}
    canonical_bytes = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    payload["receipt_hash"] = hashlib.sha256(canonical_bytes).hexdigest()
    return payload


def write_endpoint_receipt(
    path: Path,
    receipt: EndpointReceipt,
    *,
    generated_at: str,
    forbidden_values: Sequence[str] = (),
) -> dict[str, object]:
    """Atomically write one sanitized endpoint receipt."""

    payload = build_receipt_payload(
        receipt,
        generated_at=generated_at,
        forbidden_values=forbidden_values,
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(f"{serialized}\n", encoding="utf-8")
    temporary_path.replace(path)

    return payload


def _validate_sanitized_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("Receipt object keys must be strings.")

            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if normalized_key in _FORBIDDEN_FIELD_NAMES:
                raise ValueError("Receipt contains a forbidden field.")

            _validate_sanitized_value(nested_value)

        return

    if isinstance(value, list | tuple):
        for nested_value in value:
            _validate_sanitized_value(nested_value)

        return

    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed_url = urlsplit(value)

        if (
            parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Receipt URLs must not contain credentials, query strings, or fragments.")


def _reject_forbidden_values(
    payload: Mapping[str, object],
    forbidden_values: Sequence[str],
) -> None:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    for forbidden_value in forbidden_values:
        if forbidden_value and forbidden_value in serialized:
            raise ValueError("Receipt contains a forbidden sensitive value.")
