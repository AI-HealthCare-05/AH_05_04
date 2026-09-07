"""Endpoint Receipt 파일의 hash와 제품 Parser 계약을 검증합니다."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
)
from ai_worker.tasks.rag.source_client.endpoints import (
    MFDS_ENDPOINT_CANDIDATES,
)

_PRODUCT_CONTRACT = MFDS_ENDPOINT_CANDIDATES["LIST_APPROVED_PRODUCTS"].contract
_PRODUCT_OPERATION = _PRODUCT_CONTRACT.identity


@dataclass(frozen=True, slots=True)
class ReceiptFixtureEvidence:
    """Receipt에 기록된 비민감 합성 fixture 증빙입니다."""

    scenario: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProductReceiptEvidence:
    """제품 Parser가 사용하는 검증 완료 Receipt 증빙입니다."""

    receipt_version: str
    identity: SourceOperationIdentity
    validated_record_count: int
    receipt_hash: str
    fixture_evidence: tuple[ReceiptFixtureEvidence, ...]


def calculate_endpoint_receipt_hash(
    payload: Mapping[str, object],
) -> str:
    """기존 Endpoint Receipt 생성 규칙으로 SHA-256을 계산합니다."""
    canonical_payload = {key: value for key, value in payload.items() if key not in {"generated_at", "receipt_hash"}}

    try:
        canonical_bytes = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Endpoint receipt payload is not valid JSON.") from None

    return hashlib.sha256(canonical_bytes).hexdigest()


def verify_endpoint_receipt_hash(payload: dict[str, object]) -> None:
    """Receipt 내용과 기록된 SHA-256이 일치하는지 확인합니다."""
    expected_hash = payload.get("receipt_hash")

    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_hash,
    ):
        raise ValueError("Endpoint receipt hash is missing or invalid.")

    actual_hash = calculate_endpoint_receipt_hash(payload)

    if actual_hash != expected_hash:
        raise ValueError("Endpoint receipt hash mismatch.")


def _unique_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """JSON 객체의 중복 key를 거부합니다."""
    result: dict[str, object] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("Endpoint receipt contains a duplicate key.")

        result[key] = value

    return result


def _require_exact_value(
    payload: Mapping[str, object],
    key: str,
    expected: object,
) -> None:
    if payload.get(key) != expected:
        raise ValueError(f"Endpoint receipt has invalid {key}.")


def _parse_fixture_evidence(
    payload: Mapping[str, object],
) -> tuple[ReceiptFixtureEvidence, ...]:
    """Receipt의 fixture 증빙을 안전한 상대 경로로 해석합니다."""
    raw_evidence = payload.get("fixture_evidence")

    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError("Endpoint receipt has invalid fixture_evidence.")

    evidence: list[ReceiptFixtureEvidence] = []
    observed_scenarios: set[str] = set()
    observed_paths: set[str] = set()

    for raw_item in raw_evidence:
        if not isinstance(raw_item, dict):
            raise ValueError("Endpoint receipt has invalid fixture_evidence.")

        if set(raw_item) != {"scenario", "path", "sha256"}:
            raise ValueError("Endpoint receipt has invalid fixture_evidence.")

        scenario = raw_item.get("scenario")
        relative_path = raw_item.get("path")
        sha256 = raw_item.get("sha256")

        if not isinstance(scenario, str) or not re.fullmatch(r"[A-Z0-9_]+", scenario):
            raise ValueError("Endpoint receipt has invalid fixture scenario.")

        if (
            not isinstance(relative_path, str)
            or not relative_path.startswith("tests/fixtures/rag/mfds/")
            or "\\" in relative_path
            or any(segment in {"", ".", ".."} for segment in relative_path.split("/"))
        ):
            raise ValueError("Endpoint receipt has invalid fixture path.")

        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Endpoint receipt has invalid fixture checksum.")

        if scenario in observed_scenarios:
            raise ValueError("Endpoint receipt has duplicate fixture scenario.")

        if relative_path in observed_paths:
            raise ValueError("Endpoint receipt has duplicate fixture path.")

        observed_scenarios.add(scenario)
        observed_paths.add(relative_path)
        evidence.append(
            ReceiptFixtureEvidence(
                scenario=scenario,
                path=relative_path,
                sha256=sha256,
            )
        )

    return tuple(evidence)


def verify_receipt_fixture_evidence(
    *,
    evidence: tuple[ReceiptFixtureEvidence, ...],
    repository_root: Path,
) -> None:
    """Receipt에 연결된 합성 fixture 파일의 SHA-256을 확인합니다."""
    root = repository_root.resolve()

    for item in evidence:
        file_path = root.joinpath(*item.path.split("/")).resolve()

        if not file_path.is_relative_to(root):
            raise ValueError("Endpoint receipt fixture path escapes repository root.")

        try:
            content = file_path.read_bytes()
        except OSError:
            raise ValueError("Endpoint receipt fixture could not be read.") from None

        if hashlib.sha256(content).hexdigest() != item.sha256:
            raise ValueError("Endpoint receipt fixture checksum mismatch.")


def load_product_endpoint_receipt(
    path: Path,
) -> ProductReceiptEvidence:
    """제품 Parser가 사용할 수 있는 검증 완료 Receipt를 읽습니다."""
    try:
        serialized = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("Endpoint receipt could not be read.") from None

    try:
        decoded: object = json.loads(
            serialized,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("Endpoint receipt is not valid JSON.") from None

    if not isinstance(decoded, dict):
        raise ValueError("Endpoint receipt root must be an object.")

    payload = cast(dict[str, object], decoded)
    verify_endpoint_receipt_hash(payload)

    _require_exact_value(payload, "receipt_version", "1.1")
    _require_exact_value(payload, "execution_status", "COMPLETED")
    _require_exact_value(payload, "source_run_status", "SUCCEEDED")
    _require_exact_value(payload, "parser_activation_allowed", True)
    _require_exact_value(payload, "blocking_code", None)
    _require_exact_value(payload, "failure_code", None)
    _require_exact_value(
        payload,
        "primary_key_fields",
        list(_PRODUCT_CONTRACT.primary_key_fields),
    )
    _require_exact_value(payload, "primary_key_null_count", 0)
    _require_exact_value(payload, "primary_key_duplicate_count", 0)
    _require_exact_value(payload, "whole_record_duplicate_count", 0)

    _require_exact_value(
        payload,
        "verified_http_method",
        _PRODUCT_CONTRACT.method,
    )
    _require_exact_value(
        payload,
        "verified_scheme",
        _PRODUCT_CONTRACT.scheme,
    )
    _require_exact_value(
        payload,
        "verified_host",
        _PRODUCT_CONTRACT.host,
    )
    _require_exact_value(
        payload,
        "verified_path_template",
        _PRODUCT_CONTRACT.path_template,
    )
    _require_exact_value(payload, "encoding", "UTF-8")

    expected_required_parameters = [
        {
            "name": parameter.name,
            "type_name": parameter.type_name,
            "location": parameter.location,
            "sensitive": parameter.sensitive,
        }
        for parameter in _PRODUCT_CONTRACT.required_parameters
    ]
    _require_exact_value(
        payload,
        "required_parameters",
        expected_required_parameters,
    )

    _require_exact_value(
        payload,
        "body_success_code_path",
        _PRODUCT_CONTRACT.body_success_code_path,
    )
    _require_exact_value(
        payload,
        "body_error_code_path",
        _PRODUCT_CONTRACT.body_error_code_path,
    )
    _require_exact_value(
        payload,
        "body_success_codes",
        list(_PRODUCT_CONTRACT.body_codes.success_codes),
    )
    _require_exact_value(
        payload,
        "authentication_failure_codes",
        list(_PRODUCT_CONTRACT.body_codes.authentication_failure_codes),
    )
    _require_exact_value(
        payload,
        "daily_limit_codes",
        list(_PRODUCT_CONTRACT.body_codes.daily_limit_codes),
    )
    _require_exact_value(
        payload,
        "allowed_content_types",
        list(_PRODUCT_CONTRACT.allowed_content_types),
    )

    pagination = _PRODUCT_CONTRACT.pagination
    expected_pagination = {
        "mode": pagination.mode,
        "page_parameter": pagination.page_parameter,
        "page_size_parameter": pagination.page_size_parameter,
        "page_base": pagination.page_base,
        "page_size_limit": pagination.page_size_limit,
        "end_condition": pagination.end_condition,
    }
    _require_exact_value(
        payload,
        "pagination",
        expected_pagination,
    )

    limits = _PRODUCT_CONTRACT.limits
    expected_limits = {
        "connect_timeout_seconds": limits.connect_timeout_seconds,
        "read_timeout_seconds": limits.read_timeout_seconds,
        "total_timeout_seconds": limits.total_timeout_seconds,
        "max_redirects": limits.max_redirects,
        "max_response_bytes": limits.max_response_bytes,
        "max_decompressed_bytes": limits.max_decompressed_bytes,
        "max_pages": limits.max_pages,
        "max_retry_attempts": limits.max_retry_attempts,
    }
    _require_exact_value(
        payload,
        "limits",
        expected_limits,
    )
    _require_exact_value(
        payload,
        "external_version_field",
        _PRODUCT_CONTRACT.external_version_field,
    )

    identity_payload = payload.get("identity")
    expected_identity = {
        "source_code": _PRODUCT_OPERATION.source_code,
        "endpoint_code": _PRODUCT_OPERATION.endpoint_code,
        "operation_code": _PRODUCT_OPERATION.operation_code,
    }

    if identity_payload != expected_identity:
        raise ValueError("Endpoint receipt has invalid identity.")

    validated_record_count = payload.get("validated_record_count")

    if type(validated_record_count) is not int or validated_record_count <= 0:
        raise ValueError("Endpoint receipt has invalid validated_record_count.")

    fixture_evidence = _parse_fixture_evidence(payload)

    receipt_hash = payload["receipt_hash"]
    assert isinstance(receipt_hash, str)

    return ProductReceiptEvidence(
        receipt_version="1.1",
        identity=_PRODUCT_OPERATION,
        validated_record_count=validated_record_count,
        receipt_hash=receipt_hash,
        fixture_evidence=fixture_evidence,
    )
