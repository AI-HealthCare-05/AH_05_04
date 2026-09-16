"""RAG-07B Candidate Index persistence boundaries (Issue #168).

member 하나라도 바뀌면 ``member_set_hash``가 달라지는 성질을 이용해 위조를 잡아내는 경로,
``catalog_set_id``가 가리키는 Catalog Set과 불일치하는 경로, 동시 build 경쟁, 멱등 재사용,
그리고 Runtime 전용 상태가 이 테이블에 새어들지 못하는지를 검증한다.
"""

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.rag_candidate_index import (
    RagCandidateIndexBuildMode,
    RagCandidateIndexEntityType,
    RagCandidateIndexStatus,
    RagCandidateIndexVersion,
)
from app.models.rag_catalog import RagCatalogSet, RagMedicationSearchEntryType
from app.repositories.rag_candidate_index_repository import (
    CandidateIndexCatalogMismatchError,
    CandidateIndexContentHashConflictError,
    CandidateIndexEmptyMemberSetError,
    CandidateIndexMemberContentHashMismatchError,
    CandidateIndexMemberCountMismatchError,
    CandidateIndexMemberSetHashMismatchError,
    CandidateIndexVersionNotBuildableError,
    RagCandidateIndexMemberCreate,
    RagCandidateIndexRepository,
    RagCandidateIndexVersionCreate,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)

_CATALOG_VERSION = "catalog-1.0.0"
_EMBEDDING_MODEL_VERSION = "synthetic-model-v1"


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


async def _create_source_snapshot(session: AsyncSession):
    suffix = uuid4().hex[:10]
    repository = RagSourceCatalogRepository(session)
    source = await repository.create_source(
        RagSourceCreate(source_code=f"MFDS_CANDIDATE_{suffix}", display_name="MFDS Candidate Source", owner_name="MFDS")
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="LIST_PRODUCTS", display_name="List Products")
    )
    return await repository.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"api:2026-09-10:{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            collected_at=datetime.now(config.TIMEZONE),
        )
    )


async def _create_catalog_set(session: AsyncSession, *, suffix: str | None = None) -> RagCatalogSet:
    suffix = suffix or uuid4().hex[:10]
    catalog_set = RagCatalogSet(
        catalog_version=_CATALOG_VERSION,
        schema_version="schema-v1",
        normalization_version="normalization-v1",
        manifest_spec_version=f"manifest-spec-{suffix}",
        envelope_hash=_hash("9"),
        manifest_json=b"{}",
    )
    session.add(catalog_set)
    await session.flush()
    return catalog_set


def _payload_hash(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _member_content_hash(
    *,
    snapshot_id,
    entry_type: RagMedicationSearchEntryType = RagMedicationSearchEntryType.PRODUCT_NAME,
    entry_ref: str = "entry:200012345:name",
    display_text: str = "Test Tablet 500mg",
    normalized_text: str = "test tablet 500mg",
    alias_ref: str | None = None,
    alias_source_snapshot_id=None,
) -> str:
    return _payload_hash(
        {
            "identity": {
                "entity_type": RagCandidateIndexEntityType.PRODUCT.value,
                "code_system": "MFDS_ITEM_SEQ",
                "canonical_code": "200012345",
            },
            "product_ref": "product:200012345",
            "entry_ref": entry_ref,
            "entry_type": entry_type.value,
            "display_text": display_text,
            "normalized_text": normalized_text,
            "alias_ref": alias_ref,
            "product_name": "Test Tablet",
            "strength_text": None,
            "dosage_form": None,
            "manufacturer_name": None,
            "product_source_snapshot_id": str(snapshot_id),
            "entry_source_snapshot_id": str(snapshot_id),
            "alias_source_snapshot_id": str(alias_source_snapshot_id) if alias_source_snapshot_id else None,
            "catalog_version": _CATALOG_VERSION,
            "catalog_manifest_hash": _hash("9"),
            "normalization_version": "normalization-v1",
        }
    )


def _member_create(
    *,
    snapshot,
    member_key: str = "member-1",
    entry_ref: str = "entry:200012345:name",
    display_text: str = "Test Tablet 500mg",
    normalized_text: str = "test tablet 500mg",
) -> RagCandidateIndexMemberCreate:
    return RagCandidateIndexMemberCreate(
        entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
        identity_entity_type=RagCandidateIndexEntityType.PRODUCT,
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="200012345",
        product_ref="product:200012345",
        entry_ref=entry_ref,
        display_text=display_text,
        normalized_text=normalized_text,
        product_name="Test Tablet",
        product_source_snapshot_id=snapshot.id,
        entry_source_snapshot_id=snapshot.id,
        catalog_version=_CATALOG_VERSION,
        catalog_manifest_hash=_hash("9"),
        normalization_version="normalization-v1",
        member_key=member_key,
        member_content_hash=_member_content_hash(
            snapshot_id=snapshot.id,
            entry_ref=entry_ref,
            display_text=display_text,
            normalized_text=normalized_text,
        ),
    )


def _hybrid_member_create(
    *, snapshot, member_key: str = "member-1", embedding: tuple[float, ...] = (0.123456789, -0.333333333, 123.456789)
) -> RagCandidateIndexMemberCreate:
    lexical_member = _member_create(snapshot=snapshot, member_key=member_key)
    return replace(
        lexical_member,
        embedding=embedding,
        member_content_hash=_payload_hash(
            {
                "lexical_member_content_hash": lexical_member.member_content_hash,
                "embedding_model_version": _EMBEDDING_MODEL_VERSION,
                "embedding": embedding,
            }
        ),
    )


def _stable_text_sort_key(value: str) -> bytes:
    return value.encode("utf-8")


def _member_sort_key(member: RagCandidateIndexMemberCreate) -> tuple[bytes, bytes, bytes]:
    identity_key = f"{member.identity_entity_type.value}:{member.identity_code_system}:{member.identity_canonical_code}"
    return (
        _stable_text_sort_key(identity_key),
        _stable_text_sort_key(member.entry_type.value),
        _stable_text_sort_key(member.entry_ref),
    )


def _member_set_hash(members: tuple[RagCandidateIndexMemberCreate, ...]) -> str:
    return _payload_hash(
        [
            {"member_key": member.member_key, "member_content_hash": member.member_content_hash}
            for member in sorted(members, key=_member_sort_key)
        ]
    )


def _version_create(
    *,
    catalog_set: RagCatalogSet,
    members: tuple[RagCandidateIndexMemberCreate, ...],
    index_code: str,
    content_hash: str,
    index_version: str = "v1",
    build_mode: RagCandidateIndexBuildMode = RagCandidateIndexBuildMode.LEXICAL_ONLY,
) -> RagCandidateIndexVersionCreate:
    is_hybrid = build_mode is RagCandidateIndexBuildMode.HYBRID
    return RagCandidateIndexVersionCreate(
        index_code=index_code,
        index_version=index_version,
        build_mode=build_mode,
        catalog_set_id=catalog_set.id,
        catalog_version=catalog_set.catalog_version,
        catalog_manifest_hash=catalog_set.envelope_hash,
        schema_version=catalog_set.schema_version,
        normalization_version=catalog_set.normalization_version,
        lexical_config_version="lexical-v1",
        search_order_version="search-order-v1",
        candidate_limit=20,
        display_limit=10,
        member_count=len(members),
        product_identity_count=1,
        product_name_count=len(members),
        approved_alias_count=0,
        vector_count=sum(1 for member in members if member.embedding is not None),
        member_set_hash=_member_set_hash(members),
        configuration_hash=_hash("d"),
        content_hash=content_hash,
        embedding_provider="synthetic" if is_hybrid else None,
        embedding_model="synthetic-embedding" if is_hybrid else None,
        embedding_model_version=_EMBEDDING_MODEL_VERSION if is_hybrid else None,
        embedding_dimension=3 if is_hybrid else None,
        distance_metric="COSINE" if is_hybrid else None,
    )


async def _version_count(session: AsyncSession, content_hash: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(RagCandidateIndexVersion)
        .where(RagCandidateIndexVersion.content_hash == content_hash)
    )
    return int(result.scalar_one())


async def test_build_persists_version_and_every_member(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("1")
    )

    repository = RagCandidateIndexRepository(db_session)
    result = await repository.build_index_version(version=version, members=members)

    assert result.reused_existing is False
    assert result.version.status is RagCandidateIndexStatus.BUILDING
    assert result.version.catalog_set_id == catalog_set.id
    assert len(result.members) == 1
    assert result.members[0].candidate_index_version_id == result.version.id

    persisted = await repository.list_members(result.version.id)
    assert {m.id for m in persisted} == {m.id for m in result.members}


async def test_build_accepts_hybrid_member_hash_and_promotes_ready(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_hybrid_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set,
        members=members,
        index_code=index_code,
        content_hash=_hash("hybrid-ready"),
        build_mode=RagCandidateIndexBuildMode.HYBRID,
    )

    repository = RagCandidateIndexRepository(db_session)
    built = await repository.build_index_version(version=version, members=members)
    activated = await repository.activate_ready_version(built.version.id)

    assert activated.status is RagCandidateIndexStatus.READY
    assert activated.build_mode is RagCandidateIndexBuildMode.HYBRID
    assert activated.vector_count == 1
    ready = await repository.get_ready_version_by_code(index_code)
    assert ready is not None
    assert ready.id == built.version.id


async def test_build_reuses_identical_content_hash_instead_of_duplicating(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    index_code = f"idx-{uuid4().hex[:8]}"
    version = _version_create(catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("2"))

    repository = RagCandidateIndexRepository(db_session)
    first = await repository.build_index_version(version=version, members=members)
    second = await repository.build_index_version(version=version, members=members)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert second.version.id == first.version.id
    assert len(second.members) == 1


async def test_reuse_path_rejects_members_that_do_not_match_the_claimed_hash(db_session: AsyncSession) -> None:
    """재사용 경로도 신규 경로와 동일하게 incoming member를 검증한다.

    ``content_hash``와 ``version`` 메타데이터(``member_set_hash`` 포함)가 기존 저장값과
    완전히 같아도, 실제로 넘어온 member 행이 그 ``member_set_hash``를 재현하지 못하면
    재사용을 거부해야 한다. 재사용 여부만 보고 넘어가면 이미 저장된 content_hash를
    우연히(혹은 의도적으로) 다시 주장하면서 검증되지 않은 member 입력을 통과시키는
    우회로가 남는다.
    """
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("reuse-1")
    )

    repository = RagCandidateIndexRepository(db_session)
    first = await repository.build_index_version(version=version, members=members)
    assert first.reused_existing is False

    tampered_members = (replace(members[0], member_content_hash=_hash("tampered")),)
    with pytest.raises(CandidateIndexMemberContentHashMismatchError):
        await repository.build_index_version(version=version, members=tampered_members)

    # 위조 시도가 기존 저장 row나 member를 바꾸지 않았는지 확인한다.
    persisted = await repository.list_members(first.version.id)
    assert {m.member_content_hash for m in persisted} == {members[0].member_content_hash}


async def test_member_set_hash_mismatch_is_rejected(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set,
        members=members,
        index_code=f"idx-{uuid4().hex[:8]}",
        content_hash=_hash("member-set-mismatch"),
    )
    tampered_version = replace(version, member_set_hash=_hash("wrong-member-set"))

    with pytest.raises(CandidateIndexMemberSetHashMismatchError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=tampered_version, members=members)

    assert await _version_count(db_session, version.content_hash) == 0


@pytest.mark.parametrize(
    "count_field",
    ["member_count", "product_identity_count", "product_name_count", "approved_alias_count", "vector_count"],
)
async def test_member_count_mismatch_is_rejected(db_session: AsyncSession, count_field: str) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set,
        members=members,
        index_code=f"idx-{uuid4().hex[:8]}",
        content_hash=_hash(f"count-{count_field}"),
    )
    tampered_version = replace(version, **{count_field: getattr(version, count_field) + 1})

    with pytest.raises(CandidateIndexMemberCountMismatchError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=tampered_version, members=members)

    assert await _version_count(db_session, version.content_hash) == 0


async def test_content_hash_reused_with_different_content_is_refused(db_session: AsyncSession) -> None:
    """content_hash가 우연히 같아도 실제 내용이 다르면 재사용하지 않고 거부한다."""
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    shared_hash = _hash("3")
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=shared_hash
    )

    repository = RagCandidateIndexRepository(db_session)
    await repository.build_index_version(version=version, members=members)

    other_members = (_member_create(snapshot=snapshot, member_key="member-2"),)
    other_version = replace(
        _version_create(
            catalog_set=catalog_set,
            members=other_members,
            index_code=f"idx-{uuid4().hex[:8]}",
            content_hash=shared_hash,
        ),
    )

    with pytest.raises(CandidateIndexContentHashConflictError):
        await repository.build_index_version(version=other_version, members=other_members)


async def test_empty_member_set_is_refused(db_session: AsyncSession) -> None:
    catalog_set = await _create_catalog_set(db_session)
    version = _version_create(
        catalog_set=catalog_set, members=(), index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("4")
    )

    with pytest.raises(CandidateIndexEmptyMemberSetError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=version, members=())

    assert await _version_count(db_session, version.content_hash) == 0


async def test_tampered_member_content_hash_is_rejected(db_session: AsyncSession) -> None:
    """member_content_hash를 조작해도 claim된 member_set_hash와 어긋나면 저장되지 않는다."""
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("5")
    )

    tampered_members = (replace(members[0], member_content_hash=_hash("f")),)

    with pytest.raises(CandidateIndexMemberContentHashMismatchError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=version, members=tampered_members)

    assert await _version_count(db_session, version.content_hash) == 0


async def test_catalog_set_mismatch_is_rejected(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = replace(
        _version_create(
            catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("6")
        ),
        catalog_manifest_hash=_hash("0"),
    )

    with pytest.raises(CandidateIndexCatalogMismatchError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=version, members=members)

    assert await _version_count(db_session, version.content_hash) == 0


async def test_concurrent_building_versions_for_the_same_code_are_refused(db_session: AsyncSession) -> None:
    """같은 index_code에 두 번째 BUILDING row를 만들면 partial unique index가 거부한다."""
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("7"))

    repository = RagCandidateIndexRepository(db_session)
    await repository.build_index_version(version=version, members=members)

    other_members = (_member_create(snapshot=snapshot, member_key="member-2"),)
    other_version = _version_create(
        catalog_set=catalog_set, members=other_members, index_code=index_code, content_hash=_hash("8")
    )
    with pytest.raises(IntegrityError):
        await repository.build_index_version(version=other_version, members=other_members)
    await db_session.rollback()


async def test_runtime_only_status_is_rejected_by_check_constraint(db_session: AsyncSession) -> None:
    """RagRuntimeEnvironmentStatus.SUSPENDED처럼 이 테이블이 모르는 상태는 CHECK 제약이 막는다."""
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=f"idx-{uuid4().hex[:8]}", content_hash=_hash("a1")
    )
    await RagCandidateIndexRepository(db_session).build_index_version(version=version, members=members)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE rag_candidate_index_version SET status = 'SUSPENDED' WHERE content_hash = :content_hash"),
            {"content_hash": version.content_hash},
        )
    await db_session.rollback()


async def test_get_ready_version_by_code_returns_none_while_only_building(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("a2"))

    repository = RagCandidateIndexRepository(db_session)
    await repository.build_index_version(version=version, members=members)

    assert await repository.get_ready_version_by_code(index_code) is None


async def test_build_recomputes_member_content_hash_from_actual_member_fields(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (replace(_member_create(snapshot=snapshot), display_text="변조된 표시명"),)
    version = _version_create(
        catalog_set=catalog_set,
        members=members,
        index_code=f"idx-{uuid4().hex[:8]}",
        content_hash=_hash("member-content-tamper"),
    )

    with pytest.raises(CandidateIndexMemberContentHashMismatchError):
        await RagCandidateIndexRepository(db_session).build_index_version(version=version, members=members)

    assert await _version_count(db_session, version.content_hash) == 0


async def test_activate_ready_version_uses_canonical_member_order_for_member_set_hash(
    db_session: AsyncSession,
) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (
        _member_create(
            snapshot=snapshot,
            member_key="member-z",
            entry_ref="entry:200012345:z",
            display_text="Zeta Test Tablet",
            normalized_text="zeta test tablet",
        ),
        _member_create(
            snapshot=snapshot,
            member_key="member-a",
            entry_ref="entry:200012345:a",
            display_text="Alpha Test Tablet",
            normalized_text="alpha test tablet",
        ),
    )
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("ready-order")
    )

    repository = RagCandidateIndexRepository(db_session)
    built = await repository.build_index_version(version=version, members=members)
    await db_session.execute(
        text(
            "UPDATE rag_candidate_index_member "
            "SET created_at = CASE member_key "
            "WHEN 'member-z' THEN TIMESTAMPTZ '2026-09-15 00:00:00+00' "
            "WHEN 'member-a' THEN TIMESTAMPTZ '2026-09-15 00:00:01+00' "
            "ELSE created_at END "
            "WHERE candidate_index_version_id = :version_id"
        ),
        {"version_id": str(built.version.id)},
    )
    persisted = await repository.list_members(built.version.id)

    assert [member.member_key for member in persisted] == ["member-z", "member-a"]

    activated = await repository.activate_ready_version(built.version.id)

    assert activated.status is RagCandidateIndexStatus.READY


async def test_activate_ready_version_marks_building_version_ready(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("ready-1")
    )

    repository = RagCandidateIndexRepository(db_session)
    built = await repository.build_index_version(version=version, members=members)
    activated = await repository.activate_ready_version(built.version.id)

    assert activated.status is RagCandidateIndexStatus.READY
    ready = await repository.get_ready_version_by_code(index_code)
    assert ready is not None
    assert ready.id == built.version.id


async def test_activate_ready_version_retires_existing_ready_for_same_code(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    repository = RagCandidateIndexRepository(db_session)

    first_members = (_member_create(snapshot=snapshot, member_key="member-1"),)
    first_version = _version_create(
        catalog_set=catalog_set, members=first_members, index_code=index_code, content_hash=_hash("ready-old")
    )
    first = await repository.build_index_version(version=first_version, members=first_members)
    await repository.activate_ready_version(first.version.id)

    second_members = (_member_create(snapshot=snapshot, member_key="member-2"),)
    second_version = _version_create(
        catalog_set=catalog_set,
        members=second_members,
        index_code=index_code,
        content_hash=_hash("ready-new"),
        index_version="v2",
    )
    second = await repository.build_index_version(version=second_version, members=second_members)
    await repository.activate_ready_version(second.version.id)

    await db_session.refresh(first.version)
    await db_session.refresh(second.version)
    ready = await repository.get_ready_version_by_code(index_code)

    assert first.version.status is RagCandidateIndexStatus.RETIRED
    assert second.version.status is RagCandidateIndexStatus.READY
    assert ready is not None
    assert ready.id == second.version.id


async def test_activate_ready_version_rejects_incomplete_member_rows(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set,
        members=members,
        index_code=f"idx-{uuid4().hex[:8]}",
        content_hash=_hash("ready-incomplete"),
    )

    repository = RagCandidateIndexRepository(db_session)
    built = await repository.build_index_version(version=version, members=members)
    await db_session.execute(
        text("DELETE FROM rag_candidate_index_member WHERE candidate_index_version_id = :version_id"),
        {"version_id": str(built.version.id)},
    )

    with pytest.raises(CandidateIndexVersionNotBuildableError):
        await repository.activate_ready_version(built.version.id)

    await db_session.refresh(built.version)
    assert built.version.status is RagCandidateIndexStatus.BUILDING


async def test_activate_ready_version_revalidates_persisted_hybrid_member_hashes(
    db_session: AsyncSession,
) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    repository = RagCandidateIndexRepository(db_session)

    first_members = (_member_create(snapshot=snapshot, member_key="member-1"),)
    first_version = _version_create(
        catalog_set=catalog_set, members=first_members, index_code=index_code, content_hash=_hash("ready-stays")
    )
    first = await repository.build_index_version(version=first_version, members=first_members)
    await repository.activate_ready_version(first.version.id)

    second_members = (_hybrid_member_create(snapshot=snapshot, member_key="member-2"),)
    second_version = _version_create(
        catalog_set=catalog_set,
        members=second_members,
        index_code=index_code,
        content_hash=_hash("ready-hybrid-tampered"),
        index_version="v2",
        build_mode=RagCandidateIndexBuildMode.HYBRID,
    )
    second = await repository.build_index_version(version=second_version, members=second_members)
    second.members[0].embedding = [0.9, 0.2, 0.3]
    await db_session.flush()

    with pytest.raises(CandidateIndexVersionNotBuildableError):
        await repository.activate_ready_version(second.version.id)

    await db_session.refresh(first.version)
    await db_session.refresh(second.version)
    assert first.version.status is RagCandidateIndexStatus.READY
    assert second.version.status is RagCandidateIndexStatus.BUILDING


async def test_activate_ready_version_revalidates_persisted_hybrid_lexical_storage_hash(
    db_session: AsyncSession,
) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    repository = RagCandidateIndexRepository(db_session)

    first_members = (_member_create(snapshot=snapshot, member_key="member-1"),)
    first_version = _version_create(
        catalog_set=catalog_set, members=first_members, index_code=index_code, content_hash=_hash("ready-lexical-stays")
    )
    first = await repository.build_index_version(version=first_version, members=first_members)
    await repository.activate_ready_version(first.version.id)

    second_members = (_hybrid_member_create(snapshot=snapshot, member_key="member-2"),)
    second_version = _version_create(
        catalog_set=catalog_set,
        members=second_members,
        index_code=index_code,
        content_hash=_hash("ready-hybrid-lexical-tampered"),
        index_version="v2",
        build_mode=RagCandidateIndexBuildMode.HYBRID,
    )
    second = await repository.build_index_version(version=second_version, members=second_members)
    await db_session.execute(
        text(
            "UPDATE rag_candidate_index_member "
            "SET display_text = 'Tampered Display Text' "
            "WHERE candidate_index_version_id = :version_id"
        ),
        {"version_id": str(second.version.id)},
    )
    await db_session.flush()

    with pytest.raises(CandidateIndexVersionNotBuildableError):
        await repository.activate_ready_version(second.version.id)

    await db_session.refresh(first.version)
    await db_session.refresh(second.version)
    assert first.version.status is RagCandidateIndexStatus.READY
    assert second.version.status is RagCandidateIndexStatus.BUILDING


async def test_mark_failed_version_closes_only_building_version(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(
        catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("failed-1")
    )

    repository = RagCandidateIndexRepository(db_session)
    built = await repository.build_index_version(version=version, members=members)
    failed = await repository.mark_failed_version(built.version.id)

    assert failed.status is RagCandidateIndexStatus.FAILED
    assert await repository.get_ready_version_by_code(index_code) is None
    with pytest.raises(CandidateIndexVersionNotBuildableError):
        await repository.activate_ready_version(built.version.id)


async def test_ready_partial_unique_rejects_second_ready_without_retire(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    repository = RagCandidateIndexRepository(db_session)

    first_members = (_member_create(snapshot=snapshot, member_key="member-1"),)
    first_version = _version_create(
        catalog_set=catalog_set, members=first_members, index_code=index_code, content_hash=_hash("db-ready-old")
    )
    first = await repository.build_index_version(version=first_version, members=first_members)
    await repository.activate_ready_version(first.version.id)

    second_members = (_member_create(snapshot=snapshot, member_key="member-2"),)
    second_version = _version_create(
        catalog_set=catalog_set,
        members=second_members,
        index_code=index_code,
        content_hash=_hash("db-ready-new"),
        index_version="v2",
    )
    second = await repository.build_index_version(version=second_version, members=second_members)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE rag_candidate_index_version SET status = 'READY' WHERE id = :version_id"),
            {"version_id": str(second.version.id)},
        )
    await db_session.rollback()


async def test_get_version_by_code_and_version_finds_the_building_row(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    catalog_set = await _create_catalog_set(db_session)
    index_code = f"idx-{uuid4().hex[:8]}"
    members = (_member_create(snapshot=snapshot),)
    version = _version_create(catalog_set=catalog_set, members=members, index_code=index_code, content_hash=_hash("a3"))

    repository = RagCandidateIndexRepository(db_session)
    persisted = await repository.build_index_version(version=version, members=members)

    found = await repository.get_version_by_code_and_version(index_code=index_code, index_version="v1")
    assert found is not None
    assert found.id == persisted.version.id


def test_repository_exposes_no_public_member_write_path() -> None:
    """member는 build_index_version 내부에서만 쓰여진다."""
    member_methods = {
        name for name in dir(RagCandidateIndexRepository) if not name.startswith("_") and "member" in name
    }
    assert member_methods == {"list_members", "get_member_by_key"}
