"""내부 저장 자료를 기존 v2 공개 artifacts로 복원합니다. DB 조회·승인을 대체하지 않습니다."""

import dataclasses
import json

from pydantic import TypeAdapter

from ai_worker.tasks.rag.catalog.approval import CatalogApprovalReceipt, CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.build import CatalogMembers
from ai_worker.tasks.rag.catalog.export import (
    CatalogExportArtifacts,
    _approval_payload,
    _canonical_json_bytes,
    _unique_manifest_object,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.storage import CatalogStoragePlan, prepare_catalog_storage

_MEMBERS_ADAPTER = TypeAdapter(CatalogMembers)
_RECEIPT_ADAPTER = TypeAdapter(CatalogApprovalReceipt)
_RECORD_GROUPS = {
    "PRODUCT": "products",
    "INGREDIENT": "ingredients",
    "COMPONENT": "components",
    "ALIAS": "aliases",
    "SEARCH_ENTRY": "search_entries",
}


class CatalogStorageRestoreError(ValueError):
    def __init__(self) -> None:
        super().__init__("Catalog storage restoration failed")


def _require(condition: bool) -> None:
    if not condition:
        raise CatalogStorageRestoreError()


def _ordered(plan: CatalogStoragePlan) -> CatalogStoragePlan:
    # DB 조회 순서를 계약으로 삼지 않고, 중복 제거 없이 구성 전체를 비교합니다.
    return dataclasses.replace(
        plan,
        identities=tuple(sorted(plan.identities, key=lambda x: (x.entity_type, x.code_system, x.canonical_code))),
        source_refs=tuple(sorted(plan.source_refs, key=lambda x: (x.snapshot_id, x.source_version))),
        rows=tuple(sorted(plan.rows, key=lambda x: (x.kind, x.member_ref))),
        hashes=tuple(sorted(plan.hashes, key=lambda x: (x.kind, x.target))),
    )


def restore_catalog_storage(plan: CatalogStoragePlan) -> CatalogExportArtifacts:
    """구성원·출처·hash 자료를 재계산해 검증하고 전체 artifacts를 반환합니다.

    승인 receipt는 당시 기록일 뿐 현재 권한·회수·만료의 증명이 아닙니다.
    실제 DB adapter는 별도로 현재 적격성과 실행 FK를 검증해야 합니다.
    """
    try:
        return _restore(plan)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CatalogStorageRestoreError() from None


def _restore(plan: CatalogStoragePlan) -> CatalogExportArtifacts:
    groups: dict[str, list[dict[str, object]]] = {name: [] for name in _RECORD_GROUPS.values()}
    for row in plan.rows:
        record = json.loads(row.canonical_record, object_pairs_hook=_unique_manifest_object)
        _require(isinstance(record, dict))
        _require(record.pop("record_type") == row.kind)
        groups[_RECORD_GROUPS[row.kind]].append(record)
    encoded_groups = _canonical_json_bytes(groups)
    members = _MEMBERS_ADAPTER.validate_json(encoded_groups, strict=True)
    # 알 수 없는 key를 무시하거나 기본값을 채워 손상된 저장 자료를 정상화하지 않습니다.
    _require(_canonical_json_bytes(dataclasses.asdict(members)) == encoded_groups)

    manifest = json.loads(plan.manifest_json, object_pairs_hook=_unique_manifest_object)
    _require(isinstance(manifest, dict))
    receipt = None
    if manifest["approval_receipt"] is not None:
        receipt_bytes = _canonical_json_bytes(manifest["approval_receipt"])
        receipt = _RECEIPT_ADAPTER.validate_json(receipt_bytes, strict=True)
        _require(_canonical_json_bytes(dataclasses.asdict(receipt)) == receipt_bytes)
    artifacts = create_catalog_export(
        catalog_version=plan.catalog_version,
        source_refs=plan.source_refs,
        members=members,
        approval_receipt=receipt,
    )
    # counts·gate·Source 결속을 포함한 전체 manifest를 현재 producer 규칙으로 재생성합니다.
    _require(_canonical_json_bytes(json.loads(artifacts.manifest_json)) == _canonical_json_bytes(manifest))
    artifacts = dataclasses.replace(artifacts, manifest_json=plan.manifest_json)
    rebuilt = prepare_catalog_storage(members=members, artifacts=artifacts)
    _require(_ordered(rebuilt) == _ordered(plan))
    return artifacts


async def restore_current_catalog_storage(
    plan: CatalogStoragePlan, *, approval_verifier: CatalogApprovalVerifier | None
) -> CatalogExportArtifacts:
    """현재 승인 포트로 재확인한 뒤 저장 당시의 v2 bytes를 그대로 반환합니다.

    실제 승인 저장소 연결은 후속입니다. 이 확인은 호출 시점의 검사이며 DB 잠금이나
    이후 사용 시점까지의 철회 방지를 보장하지 않습니다. 새 receipt로 기존 manifest를
    다시 서명하거나 hash를 바꾸지 않습니다.
    """
    artifacts = restore_catalog_storage(plan)
    try:
        manifest = json.loads(artifacts.manifest_json)
        _require(
            manifest["verification_status"] == "APPROVED"
            and manifest["freshness_status"] == "CURRENT"
            and manifest["is_complete"] is True
            and manifest["approval_receipt"] is not None
        )
        if approval_verifier is None:
            raise CatalogStorageRestoreError()
        receipt = await approval_verifier.verify(
            catalog_version=artifacts.catalog.catalog_version,
            export_checksum=artifacts.export_checksum,
            source_refs=artifacts.catalog.source_refs,
        )
        current = _approval_payload(
            receipt,
            catalog_version=artifacts.catalog.catalog_version,
            export_checksum=artifacts.export_checksum,
            source_refs=artifacts.catalog.source_refs,
            has_entries=bool(artifacts.catalog.search_entries),
        )
        _require(_canonical_json_bytes(current) == _canonical_json_bytes({key: manifest[key] for key in current}))
    except Exception:
        # 외부 승인 저장소의 예외 원문도 소비자에게 전달하지 않습니다. 취소는 전파합니다.
        raise CatalogStorageRestoreError() from None
    return artifacts
