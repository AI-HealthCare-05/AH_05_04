from uuid import UUID

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRequest,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.service import (
    SourceAcquisitionInProgressError,
    acquire_source_exclusively,
)

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")


def _identity() -> SourceOperationIdentity:
    return SourceOperationIdentity(
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_code="MFDS_PRODUCT_APPROVAL_API",
        operation_code="LIST_APPROVED_PRODUCTS",
    )


class RecordingGate:
    def __init__(self, events: list[str], *, busy: bool = False) -> None:
        self._events = events
        self._busy = busy

    async def try_lock_acquisition(self, identity: SourceOperationIdentity) -> UUID:
        assert identity == _identity()
        self._events.append("lock")
        if self._busy:
            raise SourceAcquisitionInProgressError("synthetic busy")
        return _OPERATION_ID


class RecordingClient:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def fetch_all_pages(self, request: SourceRequest) -> SourceRunResult:
        self._events.append("fetch")
        return SourceRunResult(
            operation=request.operation,
            status=SourceRunStatus.SUCCEEDED,
            pages=(),
            failure=None,
        )


async def test_fetches_only_after_acquisition_lock() -> None:
    events: list[str] = []
    request = SourceRequest(operation=_identity(), parameters={})

    result = await acquire_source_exclusively(
        gate=RecordingGate(events),
        client=RecordingClient(events),
        request=request,
    )

    assert result.operation == request.operation
    assert events == ["lock", "fetch"]


async def test_busy_acquisition_does_not_call_provider() -> None:
    events: list[str] = []

    with pytest.raises(SourceAcquisitionInProgressError):
        await acquire_source_exclusively(
            gate=RecordingGate(events, busy=True),
            client=RecordingClient(events),
            request=SourceRequest(operation=_identity(), parameters={}),
        )

    assert events == ["lock"]
