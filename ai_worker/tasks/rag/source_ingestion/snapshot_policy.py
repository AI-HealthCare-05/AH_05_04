"""Source별 거부 Hard Limit과 빈 결과 정책을 판정합니다."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ai_worker.tasks.rag.source_client.contracts import EmptyResultPolicy


class SnapshotPolicyDecision(StrEnum):
    """수집 결과가 Snapshot 후보가 될 수 있는지 나타냅니다."""

    ALLOW_PENDING = "ALLOW_PENDING"
    REJECT = "REJECT"


class SnapshotPolicyFailureCode(StrEnum):
    """정책 판정에서 사용하는 안전한 실패 코드입니다."""

    EMPTY_RESULT = "EMPTY_RESULT"
    REJECTION_LIMIT_EXCEEDED = "REJECTION_LIMIT_EXCEEDED"


@dataclass(frozen=True, slots=True)
class SourceSnapshotPolicy:
    """Source별 Snapshot 후보 생성 정책입니다."""

    max_rejected_records: int = 0
    max_rejection_rate: Decimal = Decimal("0")
    empty_result_policy: EmptyResultPolicy = EmptyResultPolicy.REJECT

    def __post_init__(self) -> None:
        if self.max_rejected_records < 0:
            raise ValueError("max_rejected_records는 0 이상이어야 합니다.")
        if not Decimal("0") <= self.max_rejection_rate <= Decimal("1"):
            raise ValueError("max_rejection_rate는 0 이상 1 이하여야 합니다.")
        if type(self.empty_result_policy) is not EmptyResultPolicy:
            raise ValueError("지원하는 empty_result_policy가 아닙니다.")


@dataclass(frozen=True, slots=True)
class SnapshotPolicyResult:
    """Snapshot 후보 생성 전 정책 판정 결과입니다."""

    decision: SnapshotPolicyDecision
    rejection_rate: Decimal
    manual_review_required: bool
    failure_code: SnapshotPolicyFailureCode | None

    @property
    def snapshot_candidate_allowed(self) -> bool:
        return self.decision is SnapshotPolicyDecision.ALLOW_PENDING


def evaluate_snapshot_policy(
    *,
    record_count: int,
    rejected_record_count: int,
    policy: SourceSnapshotPolicy,
) -> SnapshotPolicyResult:
    """빈 결과와 두 거부 Hard Limit을 fail-closed로 판정합니다."""
    if record_count < 0:
        raise ValueError("record_count는 0 이상이어야 합니다.")
    if rejected_record_count < 0:
        raise ValueError("rejected_record_count는 0 이상이어야 합니다.")
    if rejected_record_count > record_count:
        raise ValueError("rejected_record_count는 record_count를 초과할 수 없습니다.")

    if record_count == 0:
        if policy.empty_result_policy is EmptyResultPolicy.REJECT:
            return SnapshotPolicyResult(
                decision=SnapshotPolicyDecision.REJECT,
                rejection_rate=Decimal("0"),
                manual_review_required=False,
                failure_code=SnapshotPolicyFailureCode.EMPTY_RESULT,
            )
        raise ValueError("지원하는 empty_result_policy가 아닙니다.")

    rejection_rate = Decimal(rejected_record_count) / Decimal(record_count)
    rejection_limit_exceeded = (
        rejected_record_count > policy.max_rejected_records or rejection_rate > policy.max_rejection_rate
    )

    if rejection_limit_exceeded:
        return SnapshotPolicyResult(
            decision=SnapshotPolicyDecision.REJECT,
            rejection_rate=rejection_rate,
            manual_review_required=False,
            failure_code=SnapshotPolicyFailureCode.REJECTION_LIMIT_EXCEEDED,
        )

    return SnapshotPolicyResult(
        decision=SnapshotPolicyDecision.ALLOW_PENDING,
        rejection_rate=rejection_rate,
        manual_review_required=rejected_record_count > 0,
        failure_code=None,
    )
