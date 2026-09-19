"""승인 서비스가 검증한 Catalog·Source 근거를 전달하는 내부 계약입니다."""

import json
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


class CatalogApprovalBindingError(ValueError):
    """manifest가 정확한 승인 receipt를 지목하지 못합니다. 원문을 전달하지 않습니다."""

    def __init__(self) -> None:
        super().__init__("Catalog approval binding is not exact")


@dataclass(frozen=True, slots=True)
class CatalogApprovalBinding:
    """저장된 manifest가 지목하는 정확한 승인 ID 집합.

    "가장 최근 승인"이 아니라 이 Catalog가 저장될 때 쓰인 바로 그 승인만 가리킵니다.
    """

    build_approval_id: str
    source_approval_ids: tuple[str, ...]
    catalog_version: str
    export_checksum: str
    source_refs: tuple[CandidateCatalogSourceRef, ...]

    @property
    def approval_ids(self) -> tuple[str, ...]:
        return (self.build_approval_id, *self.source_approval_ids)


def approval_binding_from_manifest(manifest_json: bytes) -> CatalogApprovalBinding:
    """저장된 manifest에서 정확한 승인 receipt ID를 읽습니다.

    승인 receipt가 없거나 Source 목록이 manifest의 `source_refs`와 정확히 일치하지 않으면
    거부합니다. 여기서 값을 보정하거나 빠진 항목을 채우지 않습니다.
    """
    try:
        manifest = json.loads(manifest_json)
        receipt = manifest["approval_receipt"]
        if receipt is None:
            raise CatalogApprovalBindingError()
        build_approval_id = receipt["receipt_id"]
        sources = receipt["sources"]
        source_ids = tuple(str(source["receipt_id"]) for source in sources)
        source_refs = tuple(
            CandidateCatalogSourceRef(
                snapshot_id=str(source["source_ref"]["snapshot_id"]),
                source_version=str(source["source_ref"]["source_version"]),
            )
            for source in sources
        )
        declared_refs = tuple(
            CandidateCatalogSourceRef(snapshot_id=str(ref["snapshot_id"]), source_version=str(ref["source_version"]))
            for ref in manifest["source_refs"]
        )
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        raise CatalogApprovalBindingError() from None
    if not isinstance(build_approval_id, str) or not build_approval_id.strip():
        raise CatalogApprovalBindingError()
    # 승인 Source 집합과 manifest가 선언한 Source 집합이 어긋나면 어느 쪽도 신뢰하지 않습니다.
    if not source_ids or len(set(source_ids)) != len(source_ids):
        raise CatalogApprovalBindingError()
    if set(source_refs) != set(declared_refs) or len(source_refs) != len(declared_refs):
        raise CatalogApprovalBindingError()
    return CatalogApprovalBinding(
        build_approval_id=build_approval_id,
        source_approval_ids=source_ids,
        catalog_version=str(manifest["catalog_version"]),
        export_checksum=str(manifest["export_checksum"]),
        source_refs=source_refs,
    )
