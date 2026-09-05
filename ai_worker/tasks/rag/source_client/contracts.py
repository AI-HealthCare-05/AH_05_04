"""Typed contracts shared by the MFDS source clients."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class RetryDisposition(StrEnum):
    """How a failed provider call may be retried."""

    NOT_RETRYABLE = "NOT_RETRYABLE"
    RETRY_AT_RESET = "RETRY_AT_RESET"
    BACKOFF = "BACKOFF"


class SourceRunStatus(StrEnum):
    """Terminal result of one source acquisition run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"


class EmptyResultPolicy(StrEnum):
    """How a completely empty provider result is handled."""

    REJECT = "REJECT"


class EndpointExecutionStatus(StrEnum):
    """Local endpoint verification execution status."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class SourceFailureCode(StrEnum):
    """Sanitized failure codes that never include provider response bodies."""

    INVALID_REQUEST = "INVALID_REQUEST"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    CONTENT_TYPE_MISMATCH = "CONTENT_TYPE_MISMATCH"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    EMPTY_RESULT = "EMPTY_RESULT"
    REDIRECT_REJECTED = "REDIRECT_REJECTED"
    DESTINATION_REJECTED = "DESTINATION_REJECTED"
    XML_SECURITY_VIOLATION = "XML_SECURITY_VIOLATION"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class SourceOperationIdentity:
    """Stable provenance identity independent of the provider URL."""

    source_code: str
    endpoint_code: str
    operation_code: str


@dataclass(frozen=True, slots=True)
class RequiredParameter:
    """One provider parameter without storing its value."""

    name: str
    type_name: str
    location: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class PaginationContract:
    """Provider pagination shape verified from documentation and responses."""

    mode: str
    page_parameter: str
    page_size_parameter: str
    page_base: int
    page_size_limit: int
    end_condition: str


@dataclass(frozen=True, slots=True)
class SourceClientLimits:
    """Hard safety limits applied to every source request."""

    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float
    max_redirects: int
    max_response_bytes: int
    max_decompressed_bytes: int
    max_pages: int
    max_retry_attempts: int


@dataclass(frozen=True, slots=True)
class ProviderBodyCodeContract:
    """Verified provider body codes for one endpoint."""

    success_codes: tuple[str, ...]
    authentication_failure_codes: tuple[str, ...]
    daily_limit_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EndpointContract:
    """Verified HTTP contract for one source operation."""

    identity: SourceOperationIdentity
    method: str
    scheme: str
    host: str
    path_template: str
    required_parameters: tuple[RequiredParameter, ...]
    allowed_content_types: tuple[str, ...]
    body_success_code_path: str
    body_error_code_path: str
    pagination: PaginationContract
    primary_key_fields: tuple[str, ...]
    external_version_field: str | None
    body_codes: ProviderBodyCodeContract
    limits: SourceClientLimits
    empty_result_policy: EmptyResultPolicy = EmptyResultPolicy.REJECT


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """Typed request passed to the common source client."""

    operation: SourceOperationIdentity
    parameters: Mapping[str, str | int]


@dataclass(frozen=True, slots=True)
class ProviderPage:
    """Sanitized successful page returned by the provider client."""

    page_number: int
    records: tuple[Mapping[str, object], ...]
    response_checksum: str
    content_type: str
    total_count: int | None = None


@dataclass(frozen=True, slots=True)
class SourceClientFailure:
    """Safe failure information suitable for logs and receipts."""

    code: SourceFailureCode
    retry: RetryDisposition
    safe_message: str
    http_status: int | None = None
    retry_reset_at: str | None = None


@dataclass(frozen=True, slots=True)
class PrimaryKeyValidationResult:
    """Sanitized primary-key statistics for one complete source run."""

    passed: bool
    record_count: int
    null_count: int
    duplicate_count: int
    missing_field_counts: tuple[tuple[str, int], ...] = ()
    candidate_field_stats: tuple[tuple[str, int, int], ...] = ()
    whole_record_duplicate_count: int = 0
    observed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    """Atomic result: failed runs never expose partial pages as candidates."""

    operation: SourceOperationIdentity
    status: SourceRunStatus
    pages: tuple[ProviderPage, ...]
    failure: SourceClientFailure | None
    primary_key_validation: PrimaryKeyValidationResult | None = None
    full_scan_completed: bool = False

    @property
    def record_count(self) -> int:
        return sum(len(page.records) for page in self.pages)

    @property
    def snapshot_candidate_allowed(self) -> bool:
        validation = self.primary_key_validation

        return (
            self.status is SourceRunStatus.SUCCEEDED
            and self.failure is None
            and self.full_scan_completed
            and self.record_count > 0
            and validation is not None
            and validation.passed
            and validation.record_count == self.record_count
        )


P0_OPERATIONS = (
    SourceOperationIdentity(
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_code="MFDS_PRODUCT_APPROVAL_API",
        operation_code="LIST_APPROVED_PRODUCTS",
    ),
    SourceOperationIdentity(
        source_code="MFDS_DUR",
        endpoint_code="MFDS_DUR_INGREDIENT_API",
        operation_code="LIST_INGREDIENT_CONTRAINDICATIONS",
    ),
    SourceOperationIdentity(
        source_code="MFDS_PATIENT_MEDICATION_GUIDE",
        endpoint_code="MFDS_PATIENT_GUIDE_API",
        operation_code="LIST_PATIENT_MEDICATION_GUIDES",
    ),
)
