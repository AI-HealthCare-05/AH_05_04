"""Focused production-chain integration test for the Authoritative Handoff Assembly (#760).

합성 authority fixture만으로는 #760 assembly가 실제 production authority를 소비한다는 것을
증명할 수 없다. 여기서는 다음 실제 경로를 그대로 통과시킨다.

```text
#712 issue_assessment_eligibility_authority
        -> RagEvidenceAuthorityRepository persist
        -> commit -> session close
        -> #746 SqlAlchemyAssessmentEligibilityAuthorityReader
        -> PersistedEvidenceAuthority
        -> #760 assembly
        -> 기존 build_guide_evidence_handoff()
        -> BUILT
```

#715 Content Hydration DB 경로는 이미
`tests/integration/rag/test_knowledge_chunk_content_hydration_postgresql.py`가 검증하므로
여기서 다시 구현하지 않는다. `HydratedGuideRetrievalSelection`은 실제 타입을 쓰되 실제로 적재한
row 값(chunk/snapshot/member/source/content hash)으로 구성한다.

production ai_worker 코드는 backend를 import하지 않는다. 여기서 #712 Repository를 쓰는 것은
테스트가 writer와 reader 양쪽 계약을 맞대기 위해서다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_evidence_authority import (
    SqlAlchemyAssessmentEligibilityAuthorityReader,
)
from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceAssemblyReason,
    AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    assemble_authoritative_guide_evidence_handoff,
)
from ai_worker.tasks.rag.evidence_authority_issuer import issue_assessment_eligibility_authority
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffReason,
    ObservedDecisionOutcome,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import AuthenticatedGuideRetrievalSelection
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import HydratedGuideRetrievalSelection
from ai_worker.tasks.rag.retrieval_run import PersistedRetrievalRunReceipt, compute_receipt_hash
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind
from app.core import config
from app.repositories.rag_evidence_authority_repository import RagEvidenceAuthorityRepository
from rag_runtime.evidence_authority import (
    IssueAssessmentAuthorityRequest,
    PersistedEvidenceAuthority,
)

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.17"
_CANONICAL_CHECKSUM = "c" * 64
_EMBEDDING_SHA256 = "e" * 64

_CHUNK_TEXTS = ("아스피린 복용 안내", "아스피린 주의사항")
_AUTHORITY_EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
_HANDOFF_EVALUATED_AT = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


def _content_hash(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "guide_handoff_assembly_760_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def _seed_two_selected_chunks(engine) -> dict[str, UUID]:
    """하나의 Source Member 아래 서로 다른 chunk 두 건을 selected hit으로 실제 적재한다."""
    ids: dict[str, UUID] = {
        key: uuid4()
        for key in (
            "user",
            "job",
            "index",
            "document",
            "source",
            "endpoint",
            "operation",
            "snapshot",
            "member",
            "run",
            "chunk_0",
            "chunk_1",
        )
    }
    suffix = uuid4().hex[:10]
    ids["external_document_id"] = ids["document"]

    async with engine.begin() as connection:
        await connection.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'hash', 'assembly760', true, false)"
            ),
            {"id": str(ids["user"]), "email": f"assembly760-{suffix}@example.com"},
        )
        await connection.execute(
            text(
                "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0)"
            ),
            {"id": str(ids["job"]), "uid": str(ids["user"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) "
                "VALUES (:id, :code, 'MFDS Evidence Source', 'DRAFT')"
            ),
            {"id": str(ids["source"]), "code": f"MFDS_A760_{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, 'PRODUCT_LIST', 'Product List', 'DRAFT', 'DISABLED', 'PENDING')"
            ),
            {"id": str(ids["endpoint"]), "source_id": str(ids["source"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, 'LIST_PRODUCTS', 'List Products', 'DISABLED', 'PENDING')"
            ),
            {"id": str(ids["operation"]), "endpoint_id": str(ids["endpoint"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, raw_manifest_checksum, canonical_checksum, schema_version, "
                "parser_version, normalization_version, canonicalization_spec_version, record_count, "
                "rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, :version, :h, :h, 'schema-v1', 'parser-v1', 'normalization-v1', "
                "'canonical-v1', 2, 0, 'PENDING', NOW())"
            ),
            {
                "id": str(ids["snapshot"]),
                "operation_id": str(ids["operation"]),
                "version": _SOURCE_VERSION,
                "h": "a" * 64,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_member "
                "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, locator, content_sha256) "
                "VALUES (:id, :snapshot_id, 'ENDPOINT_OPERATION', :endpoint_id, :operation_id, :locator, :h)"
            ),
            {
                "id": str(ids["member"]),
                "snapshot_id": str(ids["snapshot"]),
                "endpoint_id": str(ids["endpoint"]),
                "operation_id": str(ids["operation"]),
                "locator": f"product/{suffix}",
                "h": _content_hash(_CHUNK_TEXTS[0]),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                "VALUES (:id, :code, '1.0', :h, :h, :h, 'text-embedding-3-large', '1.0', 2, 'COSINE', 2)"
            ),
            {"id": str(ids["index"]), "code": f"A760_IDX_{suffix}", "h": "a" * 64},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Assembly Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'MFDS')"
            ),
            {"id": str(ids["document"]), "url": f"https://example.com/a760-{suffix}"},
        )
        for chunk_index, chunk_text in enumerate(_CHUNK_TEXTS):
            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :doc_id, :chunk_index, :chunk_text, :h, 'normalization-v1')"
                ),
                {
                    "id": str(ids[f"chunk_{chunk_index}"]),
                    "doc_id": str(ids["document"]),
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                    "h": _content_hash(chunk_text),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_knowledge_index_member "
                    "(id, knowledge_index_id, knowledge_chunk_id, source_snapshot_id, source_snapshot_member_id, "
                    "source_code, source_version, canonical_checksum, external_document_id, chunk_index, "
                    "content_hash, embedding, embedding_sha256, member_order) "
                    "VALUES (:id, :index_id, :chunk_id, :snapshot_id, :member_id, :source_code, :source_version, "
                    ":canonical, :external_id, :chunk_index, :h, '[1,0]', :embedding_sha256, :member_order)"
                ),
                {
                    "id": str(uuid4()),
                    "index_id": str(ids["index"]),
                    "chunk_id": str(ids[f"chunk_{chunk_index}"]),
                    "snapshot_id": str(ids["snapshot"]),
                    "member_id": str(ids["member"]),
                    "source_code": _SOURCE_CODE,
                    "source_version": _SOURCE_VERSION,
                    "canonical": _CANONICAL_CHECKSUM,
                    "external_id": str(ids["external_document_id"]),
                    "chunk_index": chunk_index,
                    "h": _content_hash(chunk_text),
                    "embedding_sha256": _EMBEDDING_SHA256,
                    "member_order": chunk_index + 1,
                },
            )
        await connection.execute(
            text(
                "INSERT INTO retrieval_run ("
                "id, job_id, execution_context_id, prescription_version_id, "
                "runtime_release_bundle_id, runtime_release_bundle_manifest_hash, "
                "runtime_execution_manifest_id, runtime_execution_manifest_hash, "
                "runtime_guard_decision_ref, knowledge_index_id, node_id, variant, "
                "query_digest_algorithm, query_digest_key_version, query_digest, "
                "filter_snapshot, filter_snapshot_hash, source_manifest_hash, "
                "retrieval_configuration_hash, lexical_limit, dense_limit, hybrid_limit, final_k, "
                "status, diagnostic_code, search_receipt_hash, receipt_hash, started_at, completed_at"
                ") VALUES ("
                ":id, :job_id, :ctx, :pv, :bundle, :h3, :manifest, :h4, 'ref-760', :index_id, :node, 'RET-H', "
                "'sha256', 'v1', :h1, :filter_snapshot, :h5, :h6, :h2, 20, 20, 30, 5, "
                "'COMPLETED', 'OK', :h7, :h8, NOW(), NOW())"
            ),
            {
                "id": str(ids["run"]),
                "job_id": str(ids["job"]),
                "ctx": str(uuid4()),
                "pv": str(uuid4()),
                "bundle": str(uuid4()),
                "manifest": str(uuid4()),
                "index_id": str(ids["index"]),
                "node": f"hybrid_retrieve_{suffix[:6]}",
                "filter_snapshot": json.dumps({"code": "ASPIRIN"}),
                "h1": "1" * 64,
                "h2": "2" * 64,
                "h3": "3" * 64,
                "h4": "4" * 64,
                "h5": "5" * 64,
                "h6": "6" * 64,
                "h7": "7" * 64,
                "h8": "8" * 64,
            },
        )
        for chunk_index in range(len(_CHUNK_TEXTS)):
            await connection.execute(
                text(
                    "INSERT INTO retrieval_hit ("
                    "retrieval_run_id, knowledge_chunk_id, lexical_rank, dense_rank, rrf_rank, "
                    "rrf_score, rrf_score_numerator, rrf_score_denominator, final_rank, selected"
                    ") VALUES (:run_id, :chunk_id, :rank, NULL, :rank, "
                    "0.016393442622950820, '1', '61', :rank, true)"
                ),
                {
                    "run_id": str(ids["run"]),
                    "chunk_id": str(ids[f"chunk_{chunk_index}"]),
                    "rank": chunk_index + 1,
                },
            )
    return ids


async def _issue_authorities(engine, ids: dict[str, UUID]) -> None:
    """#712 issuer로 chunk별 authority를 실제 발급하고 commit한 뒤 session을 닫는다."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for chunk_index, chunk_text in enumerate(_CHUNK_TEXTS):
            await issue_assessment_eligibility_authority(
                IssueAssessmentAuthorityRequest(
                    retrieval_run_id=ids["run"],
                    knowledge_chunk_id=ids[f"chunk_{chunk_index}"],
                    source_snapshot_id=ids["snapshot"],
                    source_snapshot_member_id=ids["member"],
                    source_code=_SOURCE_CODE,
                    source_version=_SOURCE_VERSION,
                    content_sha256=_content_hash(chunk_text),
                    evaluated_at=_AUTHORITY_EVALUATED_AT,
                ),
                RagEvidenceAuthorityRepository(session),
            )
        await session.commit()


async def _read_authorities(engine, ids: dict[str, UUID]) -> tuple[PersistedEvidenceAuthority, ...]:
    """#746 production Reader가 쓰기와 완전히 분리된 새 session에서 authority를 되읽는다."""
    reader = SqlAlchemyAssessmentEligibilityAuthorityReader(async_sessionmaker(engine, expire_on_commit=False))
    authorities: list[PersistedEvidenceAuthority] = []
    for chunk_index in range(len(_CHUNK_TEXTS)):
        authority = await reader.read_by_selection(
            retrieval_run_id=ids["run"],
            knowledge_chunk_id=ids[f"chunk_{chunk_index}"],
        )
        assert authority is not None
        authorities.append(authority)
    return tuple(authorities)


def _hydrated_selections(ids: dict[str, UUID]) -> tuple[HydratedGuideRetrievalSelection, ...]:
    """실제 적재한 row 값으로 #715 산출물을 구성한다. hydration DB 경로는 #715가 검증한다."""
    binding = RequestSourceMemberBinding(
        request_guard_ref=ImmutableArtifactRef("request_guard", "v1", "1" * 64),
        request_operation_code="get_guide",
        source_snapshot_id=ids["snapshot"],
        source_snapshot_member_id=ids["member"],
        source_code=_SOURCE_CODE,
        source_version=_SOURCE_VERSION,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=ImmutableArtifactRef("request_source_decision", "v1", "2" * 64),
        request_member_decision_ref=ImmutableArtifactRef("request_member_decision", "v1", "3" * 64),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        endpoint_code="PRODUCT_LIST",
        operation_code="LIST_PRODUCTS",
    )

    selections: list[HydratedGuideRetrievalSelection] = []
    external_document_id = str(ids["external_document_id"])
    for chunk_index, chunk_text in enumerate(_CHUNK_TEXTS):
        provenance = ProductionEvidenceProvenance(
            knowledge_index_id=ids["index"],
            index_code="A760_IDX",
            index_version="1.0",
            index_configuration_hash="a" * 64,
            knowledge_chunk_id=ids[f"chunk_{chunk_index}"],
            source_snapshot_id=ids["snapshot"],
            source_snapshot_member_id=ids["member"],
            source_code=_SOURCE_CODE,
            source_version=_SOURCE_VERSION,
            canonical_checksum=_CANONICAL_CHECKSUM,
            external_document_id=external_document_id,
            chunk_index=chunk_index,
            locator=f"doc:{external_document_id}#p{chunk_index}",
            content_hash=_content_hash(chunk_text),
            canonicalization_spec_version="canonical-v1",
            normalization_version="normalization-v1",
        )
        hit = ProductionSearchHit(
            provenance=provenance,
            coordinate=StableCoordinate(
                source_code=_SOURCE_CODE,
                source_version=_SOURCE_VERSION,
                external_document_id=external_document_id,
                chunk_index=chunk_index,
            ),
            exact_hit=False,
            observed_trigram_score=None,
            observed_fts_score=None,
            observed_dense_score=None,
            lexical_rank=chunk_index + 1,
            dense_rank=None,
            fusion_rank=chunk_index + 1,
            fraction_receipt=FractionReceipt("1", "61"),
            is_eligible_for_future_reranker=True,
        )
        selections.append(
            HydratedGuideRetrievalSelection(
                selection=AuthenticatedGuideRetrievalSelection(hit=hit, binding=binding),
                content_text=SensitiveText(chunk_text),
            )
        )
    return tuple(selections)


def _search_receipt(selections: tuple[HydratedGuideRetrievalSelection, ...]) -> ProductionSearchReceipt:
    return compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "1" * 64),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "v1", "5" * 64),
        evidence_index_ref=ImmutableArtifactRef("knowledge_index", "v1", "6" * 64),
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "v1", "2" * 64),
        adapter_artifact_ref=ImmutableArtifactRef("retrieval_adapter", "v1", "9" * 64),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="b" * 64,
        hit_manifest_sha256="f" * 64,
        selection_manifest_sha256=compute_selection_manifest_hash([hydrated.selection.hit for hydrated in selections]),
    )


def _persisted_receipt(
    ids: dict[str, UUID],
    search_receipt: ProductionSearchReceipt,
    selected_count: int,
) -> PersistedRetrievalRunReceipt:
    fields = {
        "run_id": ids["run"],
        "job_id": ids["job"],
        "node_id": "hybrid_retrieve",
        "variant": "RET-H",
        "status": "COMPLETED",
        "query_digest": "1" * 64,
        "retrieval_configuration_hash": "2" * 64,
        "source_manifest_hash": "6" * 64,
        "search_receipt_hash": search_receipt.artifact_ref.content_sha256,
        "total_signals": 4,
        "total_hits": 2,
        "selected_count": selected_count,
        "signal_manifest_hash": search_receipt.signal_manifest_sha256,
        "hit_manifest_hash": search_receipt.hit_manifest_sha256,
    }
    return PersistedRetrievalRunReceipt(
        receipt_hash=compute_receipt_hash(**fields),  # type: ignore[arg-type]
        diagnostic_code="OK",
        **fields,  # type: ignore[arg-type]
    )


def _assembly_request(
    ids: dict[str, UUID],
    authorities: tuple[PersistedEvidenceAuthority, ...],
) -> AuthoritativeGuideEvidenceHandoffAssemblyRequest:
    hydrated = _hydrated_selections(ids)
    search_receipt = _search_receipt(hydrated)
    return AuthoritativeGuideEvidenceHandoffAssemblyRequest(
        persisted_retrieval_receipt=_persisted_receipt(ids, search_receipt, len(hydrated)),
        retrieval_receipt=search_receipt,
        hydrated_selections=hydrated,
        authorities=authorities,
        evidence_keys_by_chunk={
            hydrated_selection.selection.hit.provenance.knowledge_chunk_id: f"evidence:{index}"
            for index, hydrated_selection in enumerate(hydrated, start=1)
        },
        evaluated_at=_HANDOFF_EVALUATED_AT,
    )


# ---------------------------------------------------------------------------
# #712 issuer -> persistence -> #746 Reader -> #760 assembly -> BUILT
# ---------------------------------------------------------------------------


async def test_persisted_production_authority_assembles_into_a_built_handoff(database) -> None:
    ids = await _seed_two_selected_chunks(database)
    await _issue_authorities(database, ids)
    authorities = await _read_authorities(database, ids)

    request = _assembly_request(ids, authorities)
    outcome = assemble_authoritative_guide_evidence_handoff(request)

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.BUILT
    assert outcome.reasons == ()
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.decision is GuideEvidenceHandoffBuildDecision.BUILT

    handoff = outcome.build_outcome.handoff
    assert handoff is not None
    assert handoff.evaluated_at == _HANDOFF_EVALUATED_AT
    assert handoff.retrieval_receipt_ref == request.retrieval_receipt.artifact_ref
    assert len(handoff.selections) == len(_CHUNK_TEXTS)

    # 출력 순서는 hydrated production selection 순서를 그대로 따른다.
    assert tuple(selection.knowledge_chunk_id for selection in handoff.selections) == tuple(
        hydrated.selection.hit.provenance.knowledge_chunk_id for hydrated in request.hydrated_selections
    )

    # 실제로 저장됐다 다시 읽힌 authority 값이 그대로 실려 나간다.
    persisted_by_chunk = {authority.knowledge_chunk_id: authority for authority in authorities}
    for selection in handoff.selections:
        authority = persisted_by_chunk[selection.knowledge_chunk_id]
        assert selection.eligibility_receipt_ref.content_sha256 == authority.eligibility_receipt_ref.content_sha256
        assert selection.assessment_artifact_ref.content_sha256 == authority.assessment_artifact_ref.content_sha256
        assert selection.verifier_artifact_ref.content_sha256 == authority.verifier_artifact_ref.content_sha256
        assert selection.assessment_valid_from == authority.assessment_valid_from
        assert selection.assessment_valid_until == authority.assessment_valid_until
        assert selection.content_sha256 == authority.content_sha256


async def test_assembly_does_not_write_to_the_authority_table(database) -> None:
    """#760 assembly는 pure seam이므로 persisted authority를 한 건도 바꾸지 않는다."""
    ids = await _seed_two_selected_chunks(database)
    await _issue_authorities(database, ids)
    authorities = await _read_authorities(database, ids)

    async with database.connect() as connection:
        before = (await connection.execute(text("SELECT count(*) FROM rag_evidence_authority"))).scalar_one()

    assemble_authoritative_guide_evidence_handoff(_assembly_request(ids, authorities))

    async with database.connect() as connection:
        after = (await connection.execute(text("SELECT count(*) FROM rag_evidence_authority"))).scalar_one()
        assert after == before
        assert (await _read_authorities(database, ids)) == authorities


async def test_authority_from_a_different_retrieval_run_is_rejected(database) -> None:
    """다른 run에서 발급된 실제 authority는 exact run binding에서 fail closed한다."""
    ids = await _seed_two_selected_chunks(database)
    await _issue_authorities(database, ids)
    authorities = await _read_authorities(database, ids)

    other_ids = await _seed_two_selected_chunks(database)
    await _issue_authorities(database, other_ids)
    other_authorities = await _read_authorities(database, other_ids)

    # 두 번째 run의 authority 한 건을 첫 run의 chunk id로만 옮겨 끼운다.
    foreign = other_authorities[0]
    swapped = (
        PersistedEvidenceAuthority(
            id=foreign.id,
            retrieval_run_id=foreign.retrieval_run_id,
            knowledge_chunk_id=ids["chunk_0"],
            source_snapshot_id=authorities[0].source_snapshot_id,
            source_snapshot_member_id=authorities[0].source_snapshot_member_id,
            source_code=foreign.source_code,
            source_version=foreign.source_version,
            content_sha256=foreign.content_sha256,
            eligibility_receipt_ref=foreign.eligibility_receipt_ref,
            assessment_artifact_ref=foreign.assessment_artifact_ref,
            verifier_artifact_ref=foreign.verifier_artifact_ref,
            validity_policy_ref=foreign.validity_policy_ref,
            evaluated_at=foreign.evaluated_at,
            assessment_valid_from=foreign.assessment_valid_from,
            assessment_valid_until=foreign.assessment_valid_until,
            created_at=foreign.created_at,
        ),
        authorities[1],
    )

    outcome = assemble_authoritative_guide_evidence_handoff(_assembly_request(ids, swapped))

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,)
    assert outcome.build_outcome is None


async def test_handoff_evaluated_at_outside_the_persisted_validity_window_is_rejected(database) -> None:
    """validity 판정은 새로 구현하지 않고 기존 Handoff kernel reason을 그대로 보존한다."""
    ids = await _seed_two_selected_chunks(database)
    await _issue_authorities(database, ids)
    authorities = await _read_authorities(database, ids)

    request = _assembly_request(ids, authorities)
    expired_at = authorities[0].assessment_valid_until + timedelta(seconds=1)
    outcome = assemble_authoritative_guide_evidence_handoff(
        AuthoritativeGuideEvidenceHandoffAssemblyRequest(
            persisted_retrieval_receipt=request.persisted_retrieval_receipt,
            retrieval_receipt=request.retrieval_receipt,
            hydrated_selections=request.hydrated_selections,
            authorities=request.authorities,
            evidence_keys_by_chunk=request.evidence_keys_by_chunk,
            evaluated_at=expired_at,
        )
    )

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,)
    assert outcome.build_outcome is not None
    assert GuideEvidenceHandoffReason.ASSESSMENT_EXPIRED in outcome.build_outcome.reasons
    assert outcome.build_outcome.handoff is None
