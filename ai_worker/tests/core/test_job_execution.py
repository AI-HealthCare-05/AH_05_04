"""Worker Job 실행 Repository Core 타입 테스트입니다."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    LeaseNotAcquired,
)


def set_attempt(value: object) -> None:
    """frozen dataclass의 런타임 변경 거부를 검증하기 위한 helper입니다."""

    attribute_name = "attempt"
    setattr(value, attribute_name, 2)


def test_execution_lease_is_immutable() -> None:
    lease = ExecutionLease(
        job_id=uuid4(),
        event_id=uuid4(),
        attempt=1,
        lease_token=uuid4().hex,
        lease_expires_at=datetime.now(UTC),
    )

    with pytest.raises(FrozenInstanceError):
        set_attempt(lease)


def test_committed_delivery_preserves_delivery_identity() -> None:
    job_id = uuid4()
    event_id = uuid4()

    delivery = CommittedDelivery(
        job_id=job_id,
        event_id=event_id,
        attempt=1,
    )

    assert delivery.job_id == job_id
    assert delivery.event_id == event_id
    assert delivery.attempt == 1


def test_lease_not_acquired_has_no_sensitive_details() -> None:
    result = LeaseNotAcquired()

    assert result == LeaseNotAcquired()
