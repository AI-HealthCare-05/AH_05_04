"""Source 외부 수집을 Operation 단위로 중복 실행하지 않는 경계입니다."""

from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRequest,
    SourceRunResult,
)


class SourceAcquisitionInProgressError(RuntimeError):
    """같은 Source의 수집 transaction이 이미 실행 중입니다."""


class SourceAcquisitionGate(Protocol):
    """외부 호출이 끝날 때까지 현재 transaction이 보유할 잠금 포트입니다."""

    async def try_lock_acquisition(self, identity: SourceOperationIdentity) -> UUID: ...


class SourcePageClient(Protocol):
    async def fetch_all_pages(self, request: SourceRequest) -> SourceRunResult: ...


async def acquire_source_exclusively(
    *,
    gate: SourceAcquisitionGate,
    client: SourcePageClient,
    request: SourceRequest,
) -> SourceRunResult:
    """잠금을 얻은 실행만 외부 Source의 전체 페이지를 수집합니다.

    잠금은 gate가 사용하는 DB transaction에 속합니다. 호출자는 이 함수가
    반환되거나 예외가 발생할 때까지 같은 transaction을 종료하면 안 됩니다.
    """
    await gate.try_lock_acquisition(request.operation)
    return await client.fetch_all_pages(request)
