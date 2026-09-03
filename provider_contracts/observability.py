from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class DeploymentEnvironment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Provider(StrEnum):
    CLOVA_OCR = "CLOVA_OCR"
    OPENAI = "OPENAI"


class ProviderOperation(StrEnum):
    PRESCRIPTION_RECOGNITION = "PRESCRIPTION_RECOGNITION"
    OCR_STRUCTURING = "OCR_STRUCTURING"
    GUIDE_GENERATION = "GUIDE_GENERATION"
    CHAT_GENERATION = "CHAT_GENERATION"


class ProviderFailurePhase(StrEnum):
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    TRANSPORT_CONNECTION = "TRANSPORT_CONNECTION"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_VALIDATION = "RESPONSE_VALIDATION"
    PROVIDER_POLICY = "PROVIDER_POLICY"
    APPLICATION_DEADLINE = "APPLICATION_DEADLINE"
    UNKNOWN_INTERNAL = "UNKNOWN_INTERNAL"


class ProviderErrorCode(StrEnum):
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_CONNECTION_FAILED = "PROVIDER_CONNECTION_FAILED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_REQUEST_REJECTED = "PROVIDER_REQUEST_REJECTED"
    PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
    PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
    PROVIDER_SAFETY_FILTERED = "PROVIDER_SAFETY_FILTERED"
    PROVIDER_CALL_ABORTED = "PROVIDER_CALL_ABORTED"
    PROVIDER_INTERNAL_FAILURE = "PROVIDER_INTERNAL_FAILURE"


@dataclass(frozen=True)
class ProviderCallContext:
    trace_id: str
    validation_run_id: UUID | None
    environment: DeploymentEnvironment
    validation_enabled: bool

    def __post_init__(self) -> None:
        if len(self.trace_id) != 32:
            raise ValueError("trace_id must be a 128-bit hexadecimal value")
        try:
            int(self.trace_id, 16)
        except ValueError as error:
            raise ValueError("trace_id must be a 128-bit hexadecimal value") from error
        if self.validation_run_id is not None and (
            not self.validation_enabled or self.environment is not DeploymentEnvironment.LOCAL
        ):
            raise ValueError("validation run context is allowed only for enabled local validation")


@dataclass(frozen=True)
class ProviderCallDescriptor:
    provider: Provider
    operation: ProviderOperation
    prompt_version: str | None

    def __post_init__(self) -> None:
        if self.provider is Provider.CLOVA_OCR:
            if self.operation is not ProviderOperation.PRESCRIPTION_RECOGNITION or self.prompt_version is not None:
                raise ValueError("CLOVA OCR descriptor must use prescription recognition without a prompt")
            return
        if self.operation is ProviderOperation.PRESCRIPTION_RECOGNITION:
            raise ValueError("OpenAI descriptor cannot use prescription recognition")
        if not self.prompt_version:
            raise ValueError("OpenAI descriptor requires a prompt version")
