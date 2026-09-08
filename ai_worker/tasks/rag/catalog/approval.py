"""승인 서비스가 검증한 Catalog·Source 근거를 전달하는 내부 계약입니다."""

from dataclasses import dataclass
from typing import Protocol

from ai_worker.tasks.rag.catalog.types import (
    CandidateCatalogSourceRef,
    CatalogFreshnessStatus,
    CatalogVerificationStatus,
)


@dataclass(frozen=True, slots=True)
class CatalogSourceApproval:
    source_ref: CandidateCatalogSourceRef
    receipt_id: str
    verification_status: CatalogVerificationStatus
    freshness_status: CatalogFreshnessStatus


@dataclass(frozen=True, slots=True)
class CatalogApprovalReceipt:
    receipt_id: str
    catalog_version: str
    export_checksum: str
    verification_status: CatalogVerificationStatus
    is_complete: bool
    sources: tuple[CatalogSourceApproval, ...]


class CatalogApprovalVerifier(Protocol):
    """호출자가 입력한 상태가 아니라 승인 저장소의 근거와 유효성을 검증합니다.

    실제 구현은 승인 주체·회수·유효기간 및 정확한 checksum/Source 범위를 검증해야 합니다.
    합성 구현은 테스트에만 사용하며 실제 승인 adapter는 후속 연결합니다.
    """

    async def verify(
        self,
        *,
        catalog_version: str,
        export_checksum: str,
        source_refs: tuple[CandidateCatalogSourceRef, ...],
    ) -> CatalogApprovalReceipt | None: ...
