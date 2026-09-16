import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from ai_worker.tasks.evaluation.actual_retrieval import build_actual_adapter_registry
from ai_worker.tasks.evaluation.actual_retrieval_index import (
    SYNTHETIC_INDEX_CODE,
    SYNTHETIC_INDEX_VERSION,
    DeterministicFakeEmbeddingAdapter,
    bootstrap_dev_knowledge_index,
    load_synthetic_knowledge_statements,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.common import TaskType
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchPort,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
)
from app.core import config

ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_INDEX_PATH = (
    ROOT
    / "evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
)

pytestmark = pytest.mark.asyncio


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "ret_h_eval_" + uuid4().hex
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


class CapturingSearchPort(EvidenceSearchPort):
    def __init__(self) -> None:
        self.last_search_request: EvidenceSearchRequest | None = None

    async def search(self, request: EvidenceSearchRequest) -> EvidenceSearchSuccess:
        self.last_search_request = request
        from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef

        return EvidenceSearchSuccess(
            request=request,
            adapter_artifact_ref=ImmutableArtifactRef("capture-adapter", "1.0", "0" * 64),
            lexical_hits=(),
            dense_hits=(),
            hybrid_hits=(),
            signals=(),
        )


class DummyCase:
    def __init__(self, case_id: str, query: str) -> None:
        self.case_id = case_id
        self.query = query
        self.partition = "DEV"
        self.dataset_code = "rag-natural-language-retrieval-dev"
        self.dataset_version = "1.0.0"


async def test_fresh_bootstrap_and_reuse_cycle(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)
    records = load_synthetic_knowledge_statements(SYNTHETIC_INDEX_PATH)

    # 1. Fresh bootstrap
    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    assert summary.reused is False
    assert summary.index_code == SYNTHETIC_INDEX_CODE
    assert summary.index_version == SYNTHETIC_INDEX_VERSION
    assert len(summary.source_snapshot_member_ids) == 100

    # Verify DB rows
    async with database.connect() as conn:
        sm_count = (await conn.execute(text("SELECT COUNT(*) FROM rag_source_snapshot_member"))).scalar_one()
        doc_count = (await conn.execute(text("SELECT COUNT(*) FROM knowledge_document"))).scalar_one()
        chunk_count = (await conn.execute(text("SELECT COUNT(*) FROM knowledge_chunk"))).scalar_one()
        idx_count = (await conn.execute(text("SELECT COUNT(*) FROM rag_knowledge_index"))).scalar_one()
        idx_m_count = (await conn.execute(text("SELECT COUNT(*) FROM rag_knowledge_index_member"))).scalar_one()

        assert sm_count == 100
        assert doc_count == 100
        assert chunk_count == 100
        assert idx_count == 1
        assert idx_m_count == 100

        # Verify contract fields of first and last record
        for idx, rec in [(0, records[0]), (99, records[99])]:
            row = (
                await conn.execute(
                    text(
                        "SELECT sm.locator, sm.content_sha256, sm.source_snapshot_id, "
                        "d.external_document_id, d.document_content_hash, d.document_status, "
                        "c.chunk_text, c.content_hash, c.chunk_index "
                        "FROM rag_source_snapshot_member sm "
                        "JOIN knowledge_document d ON d.source_snapshot_member_id = sm.id "
                        "JOIN knowledge_chunk c ON c.knowledge_document_id = d.id "
                        "WHERE d.external_document_id = :ext_id"
                    ),
                    {"ext_id": rec["evidence_ref_id"]},
                )
            ).mappings().one()

            assert row["locator"] == f"$.records[{idx}]"
            assert row["content_sha256"] == rec["content_sha256"]
            assert row["document_content_hash"] == rec["content_sha256"]
            assert row["document_status"] == "ACTIVE"
            assert row["chunk_text"] == rec["statement"]
            assert row["chunk_index"] == 0
            assert row["content_hash"] == rec["content_sha256"]

    # 2. Second execution (reuse)
    summary2 = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    assert summary2.reused is True
    assert summary2.knowledge_index_id == summary.knowledge_index_id
    assert summary2.source_snapshot_id == summary.source_snapshot_id
    assert summary2.source_snapshot_member_ids == summary.source_snapshot_member_ids

    # Verify row counts did not increase
    async with database.connect() as conn:
        sm_count2 = (await conn.execute(text("SELECT COUNT(*) FROM rag_source_snapshot_member"))).scalar_one()
        doc_count2 = (await conn.execute(text("SELECT COUNT(*) FROM knowledge_document"))).scalar_one()
        chunk_count2 = (await conn.execute(text("SELECT COUNT(*) FROM knowledge_chunk"))).scalar_one()
        idx_m_count2 = (await conn.execute(text("SELECT COUNT(*) FROM rag_knowledge_index_member"))).scalar_one()

        assert sm_count2 == 100
        assert doc_count2 == 100
        assert chunk_count2 == 100
        assert idx_m_count2 == 100


async def test_legacy_single_record_current_snapshot_fails_closed(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # Seed legacy CURRENT snapshot with single "$.records" member
    fixed_source_id = UUID("17810000-0000-4000-8000-000000000001")
    fixed_endpoint_id = UUID("17810000-0000-4000-8000-000000000002")
    fixed_operation_id = UUID("17810000-0000-4000-8000-000000000003")
    fixed_snapshot_id = UUID("17810000-0000-4000-8000-000000000004")
    fixed_seal_id = UUID("17810000-0000-4000-8000-000000000005")

    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'SYNTHETIC_DEV', 'Synthetic DEV Source', 'ACTIVE', 0, 0, 'REJECT')"
            ),
            {"id": str(fixed_source_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_endpoint (id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :s_id, 'SYNTHETIC_DEV_INDEX_ENDPOINT', 'Endpoint', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(fixed_endpoint_id), "s_id": str(fixed_source_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_operation (id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :e_id, 'SYNTHETIC_DEV_INDEX_RECORDS', 'Operation', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(fixed_operation_id), "e_id": str(fixed_endpoint_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :op_id, 'external:1.0.0', '1.0.0', :h, :h, 'schema-v1', "
                "'parser-v1', 'canonical-v1', '1.0.0', :h, 1, 0, 'PENDING', NOW())"
            ),
            {"id": str(fixed_snapshot_id), "op_id": str(fixed_operation_id), "h": "a" * 64},
        )
        legacy_member_id = uuid4()
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot_member "
                "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, locator, content_sha256) "
                "VALUES (:id, :s_id, 'ENDPOINT_OPERATION', :e_id, :op_id, NULL, '$.records', :h)"
            ),
            {
                "id": str(legacy_member_id),
                "s_id": str(fixed_snapshot_id),
                "e_id": str(fixed_endpoint_id),
                "op_id": str(fixed_operation_id),
                "h": "a" * 64,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :s_id, 'synthetic-dev-verification', 'PASSED', 'reviewer', NOW())"
            ),
            {"id": str(fixed_seal_id), "s_id": str(fixed_snapshot_id)},
        )
        await conn.execute(
            text(
                "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id "
                "WHERE id = :s_id"
            ),
            {"seal_id": str(fixed_seal_id), "s_id": str(fixed_snapshot_id)},
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )

    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_reused_index_tampered_fails_closed(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # First fresh bootstrap succeeds
    await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    # Tamper with a chunk text in the database
    async with database.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_chunk SET chunk_text = 'tampered statement' WHERE chunk_index = 0")
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )

    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_adapter_registry_search_binding_allows_all_100_members(database) -> None:
    class MockResolved:
        repository_root = ROOT

    capturing_search_port = CapturingSearchPort()
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    registry = build_actual_adapter_registry(
        MockResolved(),
        engine=database,
        search_port=capturing_search_port,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
    assert adapter is not None

    # Execute a search through the adapter
    req = AdapterRequest(
        run_id=str(uuid4()),
        case=DummyCase("case-001", "아세트아미노펜 500mg 복용법"),
        task_type=TaskType.RETRIEVAL,
        input_sha256="0" * 64,
        case_resource_sha256="0" * 64,
        variant_id="RET-H",
        variant_manifest_hash="0" * 64,
    )
    await adapter.execute(req)

    assert capturing_search_port.last_search_request is not None
    binding = capturing_search_port.last_search_request.execution_binding
    assert len(binding.allowed_source_snapshot_member_ids) == 100
