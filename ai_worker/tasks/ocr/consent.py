"""#207 판정과 #458 호출 직전 검사. 공개 오류/Job 상태 변환은 하지 않는다."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from ocr_runtime.structuring import OcrStructurer, OcrStructureResult
from provider_contracts.ocr import RawRecognizedField

ConsentReason = Literal[
    "LOOKUP_FAILED",
    "ACCOUNT_NOT_ACTIVE",
    "OWNER_MISMATCH",
    "MISSING_CONSENT",
    "PURPOSE_MISMATCH",
    "WITHDRAWN",
    "POLICY_VERSION_MISMATCH",
    "INVALID_CONSENT",
]


@dataclass(frozen=True)
class ConsentRow:
    purpose: str
    status: str
    policy_version: str
    timestamps_valid: bool = True


@dataclass(frozen=True)
class ConsentSnapshot:
    row: ConsentRow | None
    account_status: str
    resource_owner_matches: bool


def consent_denial(snapshot: ConsentSnapshot, *, purpose: str, policy_version: str) -> ConsentReason | None:
    if snapshot.account_status != "ACTIVE":
        return "ACCOUNT_NOT_ACTIVE"
    if not snapshot.resource_owner_matches:
        return "OWNER_MISMATCH"
    row = snapshot.row
    if row is None:
        return "MISSING_CONSENT"
    if row.purpose != purpose:
        return "PURPOSE_MISMATCH"
    if row.status == "WITHDRAWN":
        return "WITHDRAWN"
    if row.status != "GRANTED" or not row.timestamps_valid:
        return "INVALID_CONSENT"
    if not policy_version.strip() or row.policy_version != policy_version:
        return "POLICY_VERSION_MISMATCH"
    return None


class OcrConsentRepository(Protocol):
    async def get_snapshot(self, *, domain_id: UUID, job_id: UUID) -> ConsentSnapshot: ...


class OcrConsentDeniedError(Exception):
    """내부의 고정 사유만 전달한다. Worker FailureCode나 공개 API code가 아니다."""

    def __init__(self, reason: ConsentReason) -> None:
        self.reason = reason
        super().__init__(reason)


T = TypeVar("T")


@dataclass(frozen=True)
class OcrConsentGate:
    repository: OcrConsentRepository
    domain_id: UUID
    job_id: UUID
    current_policy_version: Callable[[], str]

    def __post_init__(self) -> None:
        if not self.current_policy_version().strip():
            raise ValueError("현재 OCR policy version이 필요합니다.")

    async def check(self) -> None:
        reason: ConsentReason | None
        try:
            snapshot = await self.repository.get_snapshot(domain_id=self.domain_id, job_id=self.job_id)
            reason = consent_denial(snapshot, purpose="OCR", policy_version=self.current_policy_version())
        except Exception:
            # 조회 원문·SQL·credential을 예외 chain에 연결하지 않는다. 취소는 전파한다.
            reason = "LOOKUP_FAILED"
        if reason is not None:
            raise OcrConsentDeniedError(reason)

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self.check()
        return await operation()


@dataclass(frozen=True)
class ConsentCheckedLlmStructurer:
    """Job별 Gate로 LLM 구조화 직전 새 조회. CLOVA 진입 검사와 함께 조립한다."""

    gate: OcrConsentGate
    delegate: OcrStructurer

    async def structure(self, raw_fields: list[RawRecognizedField]) -> OcrStructureResult:
        return await self.gate.run(lambda: self.delegate.structure(raw_fields))
