"""#712 Evidence Assessment·Eligibility Authority persistence: writer, exact read, immutability.

Issue #712는 "synthetic fixture만으로 Production authority persistence 완료를 선언하지 않는다"고
못박으므로, 아래 테스트는 in-memory fake가 아니라 실제 PostgreSQL 세션에 selected hit과 index
member binding을 적재한 뒤 authority를 왕복시킨다.

검증 범위:
- PD-722 validity policy projection이 공용 `canonical_json_bytes`와 byte 단위로 같다.
- selected hit / chunk source binding을 실제 스키마에서 정확히 읽는다.
- DB round-trip 후 다섯 authority 값과 네 artifact ref가 재구성 없이 exact 보존된다.
- 동일 입력 재발급은 기존 레코드를 그대로 돌려주고 validity window를 연장하지 않는다.
- 동일 identity에 다른 내용이 오면 덮어쓰지 않고 fail closed.
- 다른 run / 다른 chunk의 authority를 교차 사용할 수 없다.
- 영속된 artifact ref가 변조되면 읽기 시점에 fail closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from app.core import config
from app.models.async_jobs import AiJob, AiJobStatus, AiJobType
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentContractVersion,
    KnowledgeDocumentStatus,
    RagKnowledgeIndex,
    RagKnowledgeIndexMember,
)
from app.models.rag_evidence_authority import RagEvidenceAuthority
from app.models.rag_retrieval import RetrievalRunStatus, RetrievalRunVariant
from app.models.rag_source import (
    RagSnapshotVerificationStatus,
    RagSourceSnapshotMember,
    RagSourceSnapshotMemberKind,
)
from app.models.users import User
from app.repositories.rag_evidence_authority_repository import RagEvidenceAuthorityRepository
from app.repositories.rag_retrieval_repository import (
    RagRetrievalRepository,
    RetrievalHitCreate,
    RetrievalRunCreate,
    RetrievalRunFinalize,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.tests.fixtures.source_snapshot import seed_snapshot
from rag_runtime.evidence_authority import (
    EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
    EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256,
    EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE,
    EVIDENCE_ASSESSMENT_VALIDITY_SEMANTICS,
    EVIDENCE_ASSESSMENT_VALIDITY_VERSION,
    EvidenceAssessmentValidityPolicy,
    EvidenceAuthorityConflictError,
    EvidenceAuthorityCorruptError,
    EvidenceAuthorityErrorCode,
    ImmutableArtifactRef,
    PersistedEvidenceAuthority,
    compute_assessment_artifact_ref,
    compute_assessment_validity_window,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
)

CONTENT_SHA256 = "d" * 64
EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


@dataclass(frozen=True)
class SeededSelection:
    """실제 DB에 적재된 selected hit 하나와 그 index member binding."""

    retrieval_run_id: UUID
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    content_sha256: str


async def _seed_snapshot_and_member(db_session: AsyncSession) -> tuple[UUID, UUID, str, str]:
    suffix = uuid4().hex[:10]
    repository = RagSourceCatalogRepository(db_session)
    source = await repository.create_source(
        RagSourceCreate(
            source_code=f"MFDS_EVIDENCE_{suffix}",
            display_name="MFDS Evidence Source",
            owner_name="MFDS",
        )
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="PRODUCT_LIST",
            display_name="Product List",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_PRODUCTS",
            display_name="List Products",
        )
    )
    source_version = f"api:2026-09-17:{suffix}"
    snapshot = await seed_snapshot(
        repository,
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=source_version,
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.CURRENT,
            collected_at=datetime.now(config.TIMEZONE),
            verified_at=datetime.now(config.TIMEZONE),
        ),
    )

    member = RagSourceSnapshotMember(
        id=uuid4(),
        source_snapshot_id=snapshot.id,
        member_kind=RagSourceSnapshotMemberKind.ENDPOINT_OPERATION,
        endpoint_id=endpoint.id,
        operation_id=operation.id,
        locator=f"product/{suffix}",
        content_sha256=CONTENT_SHA256,
    )
    db_session.add(member)
    await db_session.flush()
    return snapshot.id, member.id, source.source_code, source_version


async def _seed_selection(
    db_session: AsyncSession,
    *,
    content_sha256: str = CONTENT_SHA256,
    chunk_content_sha256: str | None = None,
    selected: bool = True,
) -> SeededSelection:
    """selected hit 한 건과 대응하는 index member binding을 실제 스키마에 적재한다."""
    snapshot_id, snapshot_member_id, source_code, source_version = await _seed_snapshot_and_member(db_session)

    user = User(
        id=uuid4(),
        email=f"evidence-{uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        name="테스트",
    )
    db_session.add(user)
    await db_session.flush()

    job = AiJob(
        id=uuid4(),
        user_id=user.id,
        job_type=AiJobType.OCR,
        status=AiJobStatus.PENDING,
        max_attempts=3,
        attempt_count=0,
    )
    db_session.add(job)

    index = RagKnowledgeIndex(
        id=uuid4(),
        index_code=f"EVIDENCE_INDEX_{uuid4().hex[:8]}",
        index_version="1.0",
        corpus_manifest_hash=_hash("a"),
        embedding_manifest_hash=_hash("b"),
        index_configuration_hash=_hash("c"),
        embedding_model_ref="text-embedding-3-large",
        embedding_model_version="1.0",
        embedding_dimension=4,
        distance_metric="COSINE",
        member_count=1,
    )
    db_session.add(index)

    document = KnowledgeDocument(
        id=uuid4(),
        title="Evidence Document",
        source_url=f"https://example.com/doc/{uuid4()}",
        document_version="1.0",
        document_status=KnowledgeDocumentStatus.ACTIVE,
        record_contract_version=KnowledgeDocumentContractVersion.LEGACY_V1,
        publisher="MFDS",
    )
    db_session.add(document)
    await db_session.flush()

    chunk = KnowledgeChunk(
        id=uuid4(),
        knowledge_document_id=document.id,
        chunk_index=0,
        chunk_text="아스피린 복용 안내문",
        content_hash=chunk_content_sha256 if chunk_content_sha256 is not None else content_sha256,
        normalization_version="v1",
    )
    db_session.add(chunk)
    await db_session.flush()

    db_session.add(
        RagKnowledgeIndexMember(
            id=uuid4(),
            knowledge_index_id=index.id,
            knowledge_chunk_id=chunk.id,
            source_snapshot_id=snapshot_id,
            source_snapshot_member_id=snapshot_member_id,
            source_code=source_code,
            source_version=source_version,
            canonical_checksum=_hash("b"),
            external_document_id=f"doc-{uuid4().hex[:8]}",
            chunk_index=0,
            content_hash=content_sha256,
            embedding=[0.1, 0.2, 0.3, 0.4],
            embedding_sha256=_hash("e"),
            member_order=1,
        )
    )
    await db_session.flush()

    retrieval_repository = RagRetrievalRepository(db_session)
    run, _ = await retrieval_repository.begin_run(
        RetrievalRunCreate(
            job_id=job.id,
            node_id=f"hybrid_retrieve_{uuid4().hex[:6]}",
            execution_context_id=uuid4(),
            prescription_version_id=uuid4(),
            runtime_release_bundle_id=uuid4(),
            runtime_release_bundle_manifest_hash=_hash("3"),
            runtime_execution_manifest_id=uuid4(),
            runtime_execution_manifest_hash=_hash("4"),
            runtime_guard_decision_ref="ref-712",
            knowledge_index_id=index.id,
            variant=RetrievalRunVariant.RET_H.value,
            query_digest_algorithm="sha256",
            query_digest_key_version="v1",
            query_digest=_hash("1"),
            filter_snapshot={"code": "ASPIRIN"},
            filter_snapshot_hash=_hash("5"),
            source_manifest_hash=_hash("6"),
            retrieval_configuration_hash=_hash("2"),
            lexical_limit=20,
            dense_limit=20,
            hybrid_limit=30,
            final_k=5,
        )
    )
    await retrieval_repository.finalize_run(
        run.id,
        RetrievalRunFinalize(
            status=RetrievalRunStatus.COMPLETED.value,
            search_receipt_hash=_hash("e"),
            receipt_hash=_hash("f"),
            hits=(
                RetrievalHitCreate(
                    knowledge_chunk_id=chunk.id,
                    rrf_rank=1,
                    rrf_score=Decimal("0.016393442622950820"),
                    rrf_score_numerator="1",
                    rrf_score_denominator="61",
                    final_rank=1,
                    selected=selected,
                ),
            ),
        ),
    )

    return SeededSelection(
        retrieval_run_id=run.id,
        knowledge_chunk_id=chunk.id,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=snapshot_member_id,
        source_code=source_code,
        source_version=source_version,
        content_sha256=content_sha256,
    )


def _make_authority_record(
    selection: SeededSelection,
    *,
    evaluated_at: datetime = EVALUATED_AT,
    policy: EvidenceAssessmentValidityPolicy | None = None,
) -> PersistedEvidenceAuthority:
    """Issuer와 같은 계약 함수로만 authority 레코드를 만든다 (임의 ref 금지)."""
    effective_policy = policy or EvidenceAssessmentValidityPolicy()
    policy_ref = compute_validity_policy_ref(effective_policy)
    valid_from, valid_until = compute_assessment_validity_window(
        evaluated_at=evaluated_at,
        policy=effective_policy,
        applicable_upper_bounds=(),
    )
    verifier_ref = compute_verifier_artifact_ref()
    eligibility_ref = compute_eligibility_receipt_ref(
        retrieval_run_id=selection.retrieval_run_id,
        knowledge_chunk_id=selection.knowledge_chunk_id,
        source_snapshot_id=selection.source_snapshot_id,
        source_snapshot_member_id=selection.source_snapshot_member_id,
        source_code=selection.source_code,
        source_version=selection.source_version,
        content_sha256=selection.content_sha256,
        evaluated_at=evaluated_at,
        verifier_artifact_ref=verifier_ref,
    )
    assessment_ref = compute_assessment_artifact_ref(
        retrieval_run_id=selection.retrieval_run_id,
        knowledge_chunk_id=selection.knowledge_chunk_id,
        eligibility_receipt_ref=eligibility_ref,
        validity_policy_ref=policy_ref,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )
    return PersistedEvidenceAuthority(
        id=uuid4(),
        retrieval_run_id=selection.retrieval_run_id,
        knowledge_chunk_id=selection.knowledge_chunk_id,
        source_snapshot_id=selection.source_snapshot_id,
        source_snapshot_member_id=selection.source_snapshot_member_id,
        source_code=selection.source_code,
        source_version=selection.source_version,
        content_sha256=selection.content_sha256,
        eligibility_receipt_ref=eligibility_ref,
        assessment_artifact_ref=assessment_ref,
        verifier_artifact_ref=verifier_ref,
        validity_policy_ref=policy_ref,
        evaluated_at=evaluated_at,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )


def test_jcs_byte_equivalence_with_canonical_json_bytes() -> None:
    """PD-722 §7.1: 정책 projection이 공용 직렬화 모듈과 byte 단위로 같고 golden hash를 만든다."""
    policy_payload: dict[str, JsonValue] = {
        "max_validity_duration_seconds": EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
        "policy_code": EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE,
        "semantics": EVIDENCE_ASSESSMENT_VALIDITY_SEMANTICS,
        "version": EVIDENCE_ASSESSMENT_VALIDITY_VERSION,
    }

    jcs_bytes = canonical_json_bytes(policy_payload)
    py_bytes = json.dumps(policy_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert jcs_bytes == py_bytes

    assert compute_validity_policy_ref().content_sha256 == EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256


async def test_selected_hit_and_source_binding_read_from_real_schema(db_session: AsyncSession) -> None:
    """적재된 selected hit과 index member binding을 실제 스키마에서 정확히 읽는다."""
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)

    assert await repository.get_selected_hit(selection.retrieval_run_id, selection.knowledge_chunk_id) is True
    assert await repository.get_selected_hit(selection.retrieval_run_id, uuid4()) is None

    binding = await repository.get_chunk_source_binding(selection.retrieval_run_id, selection.knowledge_chunk_id)
    assert binding is not None
    assert binding.source_snapshot_id == selection.source_snapshot_id
    assert binding.source_snapshot_member_id == selection.source_snapshot_member_id
    assert binding.source_code == selection.source_code
    assert binding.source_version == selection.source_version
    assert binding.content_sha256 == selection.content_sha256


async def test_unselected_hit_is_reported_as_not_selected(db_session: AsyncSession) -> None:
    """selected=False인 hit은 없음(None)이 아니라 미선택(False)으로 구분되어야 fail-closed가 성립한다."""
    selection = await _seed_selection(db_session, selected=False)
    repository = RagEvidenceAuthorityRepository(db_session)

    assert await repository.get_selected_hit(selection.retrieval_run_id, selection.knowledge_chunk_id) is False


async def test_chunk_and_index_member_hash_disagreement_refuses_binding(db_session: AsyncSession) -> None:
    """index member와 knowledge_chunk의 content_hash가 어긋나면 binding을 인정하지 않는다.

    Production `PostgreSqlEvidenceEligibilityVerifier`가 두 값의 일치를 요구하므로, authority
    writer도 같은 기준으로 fail closed여야 한다.
    """
    selection = await _seed_selection(db_session, content_sha256=CONTENT_SHA256, chunk_content_sha256=_hash("9"))
    repository = RagEvidenceAuthorityRepository(db_session)

    assert await repository.get_chunk_source_binding(selection.retrieval_run_id, selection.knowledge_chunk_id) is None


async def test_persisted_authority_round_trips_without_reconstruction(db_session: AsyncSession) -> None:
    """DB round-trip 후 다섯 authority 값과 네 artifact ref가 exact 보존된다."""
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)
    record = _make_authority_record(selection)

    persisted = await repository.persist_authority(record)
    # created_at만 서버 기본값으로 채워지고 나머지는 입력 그대로여야 한다.
    assert persisted.created_at is not None
    assert replace(persisted, created_at=None) == record

    db_session.expire_all()
    loaded = await repository.get_authority_by_identity(selection.retrieval_run_id, selection.knowledge_chunk_id)
    assert loaded is not None

    assert loaded.eligibility_receipt_ref == record.eligibility_receipt_ref
    assert loaded.assessment_artifact_ref == record.assessment_artifact_ref
    assert loaded.verifier_artifact_ref == record.verifier_artifact_ref
    assert loaded.validity_policy_ref == record.validity_policy_ref
    assert loaded.evaluated_at == EVALUATED_AT
    assert loaded.assessment_valid_from == EVALUATED_AT
    assert loaded.assessment_valid_until == EVALUATED_AT + timedelta(
        seconds=EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS
    )
    assert loaded.assessment_valid_from <= loaded.evaluated_at < loaded.assessment_valid_until
    assert loaded.source_snapshot_id == selection.source_snapshot_id
    assert loaded.source_snapshot_member_id == selection.source_snapshot_member_id
    assert loaded.content_sha256 == selection.content_sha256
    assert loaded.created_at is not None

    by_ref = await repository.get_authority_by_assessment_ref(record.assessment_artifact_ref)
    assert by_ref is not None
    assert by_ref.id == record.id


async def test_retry_returns_existing_and_does_not_extend_validity(db_session: AsyncSession) -> None:
    """같은 입력 재발급은 기존 레코드를 그대로 돌려주고 validity window를 연장하지 않는다."""
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)
    first = await repository.persist_authority(_make_authority_record(selection))

    retried_at = EVALUATED_AT + timedelta(minutes=10)
    retry_record = replace(
        _make_authority_record(selection),
        evaluated_at=retried_at,
        assessment_valid_from=retried_at,
        assessment_valid_until=retried_at + timedelta(seconds=EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS),
    )
    second = await repository.persist_authority(retry_record)

    assert second.id == first.id
    assert second.assessment_valid_from == first.assessment_valid_from
    assert second.assessment_valid_until == first.assessment_valid_until

    rows = list(
        await db_session.scalars(RagEvidenceAuthority.__table__.select().with_only_columns(RagEvidenceAuthority.id))
    )
    assert len(rows) == 1


async def test_conflicting_details_for_same_identity_fail_closed(db_session: AsyncSession) -> None:
    """동일 identity에 다른 내용이 오면 덮어쓰지 않고 fail closed."""
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)
    original = await repository.persist_authority(_make_authority_record(selection))

    conflicting = replace(_make_authority_record(selection), source_snapshot_id=uuid4())
    with pytest.raises(EvidenceAuthorityConflictError) as error:
        await repository.persist_authority(conflicting)
    assert error.value.code == EvidenceAuthorityErrorCode.AUTHORITY_IDENTITY_CONFLICT

    unchanged = await repository.get_authority_by_identity(selection.retrieval_run_id, selection.knowledge_chunk_id)
    assert unchanged is not None
    assert unchanged.source_snapshot_id == original.source_snapshot_id


async def test_authority_cannot_be_swapped_across_runs_or_chunks(db_session: AsyncSession) -> None:
    """다른 retrieval run의 authority를 같은 lookup key로 꺼내 쓸 수 없다."""
    first = await _seed_selection(db_session)
    second = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)

    first_authority = await repository.persist_authority(_make_authority_record(first))
    second_authority = await repository.persist_authority(_make_authority_record(second))

    assert first_authority.id != second_authority.id
    assert first_authority.assessment_artifact_ref != second_authority.assessment_artifact_ref

    fetched = await repository.get_authority_by_identity(first.retrieval_run_id, second.knowledge_chunk_id)
    assert fetched is None

    by_ref = await repository.get_authority_by_assessment_ref(second_authority.assessment_artifact_ref)
    assert by_ref is not None
    assert by_ref.retrieval_run_id == second.retrieval_run_id


async def test_tampered_artifact_digest_is_rejected_by_database(db_session: AsyncSession) -> None:
    """영속된 artifact digest는 스키마 제약이 직접 거부한다 (조용한 변조 불가)."""
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)
    record = await repository.persist_authority(_make_authority_record(selection))

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(RagEvidenceAuthority)
                .where(RagEvidenceAuthority.id == record.id)
                .values(verifier_artifact_sha256="not-a-sha256")
            )

    db_session.expire_all()
    intact = await repository.get_authority_by_identity(selection.retrieval_run_id, selection.knowledge_chunk_id)
    assert intact is not None
    assert intact.verifier_artifact_ref == record.verifier_artifact_ref


async def test_corrupt_artifact_ref_fails_closed_on_read(db_session: AsyncSession) -> None:
    """제약이 잡지 못하는 변조(빈 artifact_code)는 읽기 시점에 fail closed여야 한다.

    `chk_*_sha256`은 digest만 검사하므로 artifact_code를 비우는 변조는 DB를 통과한다.
    이때 Repository가 조용히 반쪽짜리 ref를 돌려주면 후속 Reader가 authority를 오인하므로,
    `EvidenceAuthorityCorruptError`로 멈춰야 한다.
    """
    selection = await _seed_selection(db_session)
    repository = RagEvidenceAuthorityRepository(db_session)
    record = await repository.persist_authority(_make_authority_record(selection))

    await db_session.execute(
        update(RagEvidenceAuthority).where(RagEvidenceAuthority.id == record.id).values(verifier_artifact_code="")
    )
    await db_session.flush()
    db_session.expire_all()

    with pytest.raises(EvidenceAuthorityCorruptError) as error:
        await repository.get_authority_by_identity(selection.retrieval_run_id, selection.knowledge_chunk_id)
    assert error.value.code == EvidenceAuthorityErrorCode.CORRUPT_AUTHORITY_RECORD


def test_persisted_authority_artifact_refs_are_well_formed() -> None:
    """네 artifact ref 모두 ImmutableArtifactRef 의미(코드/버전/64-hex)를 따른다."""
    selection = SeededSelection(
        retrieval_run_id=uuid4(),
        knowledge_chunk_id=uuid4(),
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code="MFDS",
        source_version="2026.09.17",
        content_sha256=CONTENT_SHA256,
    )
    record = _make_authority_record(selection)
    for ref in (
        record.eligibility_receipt_ref,
        record.assessment_artifact_ref,
        record.verifier_artifact_ref,
        record.validity_policy_ref,
    ):
        assert isinstance(ref, ImmutableArtifactRef)
        assert len(ref.content_sha256) == 64
        assert ref.artifact_code.strip()
        assert ref.version.strip()
