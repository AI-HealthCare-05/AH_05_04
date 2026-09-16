import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
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
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchPort,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
)
from app.core import config

ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_INDEX_PATH = (
    ROOT / "evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
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
                (
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
                )
                .mappings()
                .one()
            )

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
        await conn.execute(text("UPDATE knowledge_chunk SET chunk_text = 'tampered statement' WHERE chunk_index = 0"))

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


async def test_pending_30_to_100_recovery(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)
    records = load_synthetic_knowledge_statements(SYNTHETIC_INDEX_PATH)

    fixed_source_id = UUID("17810000-0000-4000-8000-000000000001")
    fixed_endpoint_id = UUID("17810000-0000-4000-8000-000000000002")
    fixed_operation_id = UUID("17810000-0000-4000-8000-000000000003")
    fixed_snapshot_id = UUID("17810000-0000-4000-8000-000000000004")
    file_sha256 = hashlib.sha256(SYNTHETIC_INDEX_PATH.read_bytes()).hexdigest()

    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status, max_rejected_records, max_rejection_rate, empty_result_policy) "
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
                "'parser-v1', 'canonical-v1', '1.0.0', :h, 100, 0, 'PENDING', NOW())"
            ),
            {"id": str(fixed_snapshot_id), "op_id": str(fixed_operation_id), "h": file_sha256},
        )
        for i in range(30):
            await conn.execute(
                text(
                    "INSERT INTO rag_source_snapshot_member "
                    "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, locator, content_sha256) "
                    "VALUES (:id, :s_id, 'ENDPOINT_OPERATION', :e_id, :op_id, NULL, :loc, :c_hash)"
                ),
                {
                    "id": str(uuid4()),
                    "s_id": str(fixed_snapshot_id),
                    "e_id": str(fixed_endpoint_id),
                    "op_id": str(fixed_operation_id),
                    "loc": f"$.records[{i}]",
                    "c_hash": records[i]["content_sha256"],
                },
            )

    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    assert summary.reused is False
    assert len(summary.source_snapshot_member_ids) == 100

    async with database.connect() as conn:
        snap = (
            (
                await conn.execute(
                    text("SELECT verification_status, verification_seal_id FROM rag_source_snapshot WHERE id = :id"),
                    {"id": str(fixed_snapshot_id)},
                )
            )
            .mappings()
            .one()
        )
        assert snap["verification_status"] == "CURRENT"
        assert snap["verification_seal_id"] is not None

        member_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM rag_source_snapshot_member WHERE source_snapshot_id = :id"),
                {"id": str(fixed_snapshot_id)},
            )
        ).scalar_one()
        assert member_count == 100


async def test_pending_30_with_legacy_member_fails_closed(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)
    records = load_synthetic_knowledge_statements(SYNTHETIC_INDEX_PATH)

    fixed_source_id = UUID("17810000-0000-4000-8000-000000000001")
    fixed_endpoint_id = UUID("17810000-0000-4000-8000-000000000002")
    fixed_operation_id = UUID("17810000-0000-4000-8000-000000000003")
    fixed_snapshot_id = UUID("17810000-0000-4000-8000-000000000004")
    file_sha256 = hashlib.sha256(SYNTHETIC_INDEX_PATH.read_bytes()).hexdigest()

    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status, max_rejected_records, max_rejection_rate, empty_result_policy) "
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
                "'parser-v1', 'canonical-v1', '1.0.0', :h, 100, 0, 'PENDING', NOW())"
            ),
            {"id": str(fixed_snapshot_id), "op_id": str(fixed_operation_id), "h": file_sha256},
        )
        for i in range(30):
            await conn.execute(
                text(
                    "INSERT INTO rag_source_snapshot_member "
                    "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, locator, content_sha256) "
                    "VALUES (:id, :s_id, 'ENDPOINT_OPERATION', :e_id, :op_id, NULL, :loc, :c_hash)"
                ),
                {
                    "id": str(uuid4()),
                    "s_id": str(fixed_snapshot_id),
                    "e_id": str(fixed_endpoint_id),
                    "op_id": str(fixed_operation_id),
                    "loc": f"$.records[{i}]",
                    "c_hash": records[i]["content_sha256"],
                },
            )
        # Add out-of-range member
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot_member "
                "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, locator, content_sha256) "
                "VALUES (:id, :s_id, 'ENDPOINT_OPERATION', :e_id, :op_id, NULL, '$.records[101]', :c_hash)"
            ),
            {
                "id": str(uuid4()),
                "s_id": str(fixed_snapshot_id),
                "e_id": str(fixed_endpoint_id),
                "op_id": str(fixed_operation_id),
                "c_hash": "e" * 64,
            },
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_fake_built_index_rejected_by_actual_openai(database) -> None:
    fake_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # 1. Bootstrap index with fake adapter
    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=fake_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    assert summary.reused is False

    # 2. Simulate OpenAI adapter with production model ref
    class MockOpenAIEmbeddingAdapter:
        model_ref = "openai:text-embedding-3-large"
        model_version = "text-embedding-3-large"
        dimension = 1536
        _adapter_artifact_ref = ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64)

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=MockOpenAIEmbeddingAdapter(),
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_missing_credential_blocks_actual_execution(database, monkeypatch) -> None:
    class MockResolved:
        repository_root = ROOT

    capturing_search_port = CapturingSearchPort()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # First pre-populate with an openai-model index so the repository has a valid production index
    class MockOpenAIEmbeddingAdapter:
        model_ref = "openai:text-embedding-3-large"
        model_version = "text-embedding-3-large"
        dimension = 1536
        _adapter_artifact_ref = ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64)

        async def embed(self, texts, **kwargs):
            from ai_worker.tasks.rag.evidence_search import SensitiveVector
            from ai_worker.tasks.rag.text_embedding import TextEmbeddingSuccess

            vec = SensitiveVector(tuple(0.001 for _ in range(1536)))
            return TextEmbeddingSuccess(embedding=vec, adapter_artifact_ref=self._adapter_artifact_ref)

    init_summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=MockOpenAIEmbeddingAdapter(),
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    assert init_summary.reused is False

    # Build registry without credentials and without passing text_embedding_port
    registry = build_actual_adapter_registry(
        MockResolved(),
        engine=database,
        search_port=capturing_search_port,
        text_embedding_port=None,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
    assert adapter is not None

    req_hybrid = AdapterRequest(
        run_id=str(uuid4()),
        case=DummyCase("case-hybrid", "타이레놀 복용법"),
        task_type=TaskType.RETRIEVAL,
        input_sha256="0" * 64,
        case_resource_sha256="0" * 64,
        variant_id="RET-H",
        variant_manifest_hash="0" * 64,
    )
    res_hybrid = await adapter.execute(req_hybrid)
    assert res_hybrid.execution_status == "INVALID"
    assert "BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL" in res_hybrid.failure_codes

    req_dense = AdapterRequest(
        run_id=str(uuid4()),
        case=DummyCase("case-dense", "타이레놀 복용법"),
        task_type=TaskType.RETRIEVAL,
        input_sha256="0" * 64,
        case_resource_sha256="0" * 64,
        variant_id="RET-D",
        variant_manifest_hash="0" * 64,
    )
    res_dense = await adapter.execute(req_dense)
    assert res_dense.execution_status == "INVALID"
    assert "BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL" in res_dense.failure_codes

    req_lexical = AdapterRequest(
        run_id=str(uuid4()),
        case=DummyCase("case-lexical", "타이레놀 복용법"),
        task_type=TaskType.RETRIEVAL,
        input_sha256="0" * 64,
        case_resource_sha256="0" * 64,
        variant_id="RET-L",
        variant_manifest_hash="0" * 64,
    )
    res_lexical = await adapter.execute(req_lexical)
    assert res_lexical.execution_status == "COMPLETED"


async def test_manifest_ref_and_bridge_stable_key_exact_match(database) -> None:
    from ai_worker.tasks.evaluation.loaders import load_dataset

    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)
    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    receipt = summary.receipt

    dataset_path = ROOT / "evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
    evals_root = ROOT / "evals"
    validated = load_dataset(dataset_path, evals_root=evals_root)

    assert receipt.dataset_ref.id == validated.manifest.dataset_code
    assert receipt.dataset_ref.version == validated.manifest.dataset_version
    assert receipt.dataset_ref.hash == validated.manifest.manifest_sha256

    assert receipt.evidence_mapping_ref.id == validated.evidence_mapping.mapping_id
    assert receipt.evidence_mapping_ref.version == validated.evidence_mapping.mapping_version
    assert receipt.evidence_mapping_ref.hash == validated.evidence_mapping.manifest_sha256

    # Verify provenance is DRAFT without fabricated reviewers/evidence
    assert receipt.built_by.team_gold_status.value == "DRAFT"
    assert receipt.built_by.reviewed_by is None
    assert receipt.built_by.reviewed_at is None
    assert receipt.built_by.approved_by is None
    assert receipt.built_by.approved_at is None
    assert len(receipt.built_by.evidence_review_refs) == 0

    # Bridge entries match the 20 golden chunks
    assert len(receipt.bridge_entries) == 20
    expected_stable_keys = {f"SYNTHETIC_NLR_CHUNK_{i:03d}" for i in range(1, 21)}
    actual_stable_keys = {e.evidence_mapping_stable_key for e in receipt.bridge_entries}
    assert actual_stable_keys == expected_stable_keys


async def test_reused_index_unsealed_snapshot_rejected(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # First fresh bootstrap succeeds
    await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    # Tamper with snapshot: set verification_status back to PENDING and remove seal_id (with verified_at/effective_at NULL)
    async with database.begin() as conn:
        await conn.execute(
            text(
                "UPDATE rag_source_snapshot "
                "SET verification_status = 'PENDING', verification_seal_id = NULL, "
                "verified_at = NULL, effective_at = NULL "
                "WHERE id = '17810000-0000-4000-8000-000000000004'"
            )
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_legacy_identity_detected_and_fails_closed(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # Seed legacy entity (rag_source with 17800000-* ID)
    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status, max_rejected_records, max_rejection_rate, empty_result_policy) "
                "VALUES ('17800000-0000-4000-8000-000000000001', 'LEGACY_SOURCE', 'Legacy Source', 'ACTIVE', 0, 0, 'REJECT')"
            )
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_cli_async_lifecycle_execution(database) -> None:
    class MockResolved:
        repository_root = ROOT

    capturing_search_port = CapturingSearchPort()
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # Use a NullPool engine to test cross-loop CLI lifecycle cleanly without connection leaks
    cli_engine = create_async_engine(database.url, poolclass=NullPool, hide_parameters=True)
    try:

        def _create_registry():
            return build_actual_adapter_registry(
                MockResolved(),
                engine=cli_engine,
                search_port=capturing_search_port,
                text_embedding_port=embedding_adapter,
                synthetic_index_path=SYNTHETIC_INDEX_PATH,
            )

        registry = await asyncio.to_thread(_create_registry)
        adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
        assert adapter is not None

        req = AdapterRequest(
            run_id=str(uuid4()),
            case=DummyCase("case-cli", "아세트아미노펜 복용법"),
            task_type=TaskType.RETRIEVAL,
            input_sha256="0" * 64,
            case_resource_sha256="0" * 64,
            variant_id="RET-H",
            variant_manifest_hash="0" * 64,
        )
        res = await adapter.execute(req)
        assert res.execution_status == "COMPLETED"

        # Execute in a separate event loop across threads
        def _run_in_separate_loop():
            async def _exec():
                return await adapter.execute(req)

            return asyncio.run(_exec())

        res2 = await asyncio.to_thread(_run_in_separate_loop)
        assert res2.execution_status == "COMPLETED"
    finally:
        await cli_engine.dispose()


async def test_reused_index_unapproved_endpoint_acquisition_rejected(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    async with database.begin() as conn:
        await conn.execute(
            text(
                "UPDATE rag_source_endpoint "
                "SET acquisition_status = 'PENDING' "
                "WHERE id = '17810000-0000-4000-8000-000000000002'"
            )
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_reused_index_unapproved_operation_acquisition_rejected(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )

    async with database.begin() as conn:
        await conn.execute(
            text(
                "UPDATE rag_source_operation "
                "SET acquisition_status = 'REVOKED' "
                "WHERE id = '17810000-0000-4000-8000-000000000003'"
            )
        )

    with pytest.raises(EvaluationValidationError) as exc_info:
        await bootstrap_dev_knowledge_index(
            database,
            text_embedding_port=embedding_adapter,
            synthetic_index_path=SYNTHETIC_INDEX_PATH,
        )
    assert exc_info.value.code == EvaluationErrorCode.REPOSITORY_STATE_INVALID


async def test_unrelated_source_with_records_locator_does_not_fail_closed(database) -> None:
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)
    unrelated_source_id = UUID("99990000-0000-4000-8000-000000000001")
    unrelated_ep_id = UUID("99990000-0000-4000-8000-000000000002")
    unrelated_op_id = UUID("99990000-0000-4000-8000-000000000003")
    unrelated_snap_id = UUID("99990000-0000-4000-8000-000000000004")
    unrelated_member_id = UUID("99990000-0000-4000-8000-000000000005")

    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status, max_rejected_records, max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'UNRELATED_SOURCE', 'Unrelated Source', 'ACTIVE', 0, 0, 'REJECT')"
            ),
            {"id": str(unrelated_source_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_endpoint (id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :s_id, 'UNRELATED_EP', 'Unrelated Endpoint', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(unrelated_ep_id), "s_id": str(unrelated_source_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_operation (id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :e_id, 'UNRELATED_OP', 'Unrelated Operation', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(unrelated_op_id), "e_id": str(unrelated_ep_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :op_id, 'unrelated:1.0.0', '1.0.0', :h, :h, 'schema-v1', "
                "'parser-v1', 'canonical-v1', '1.0.0', :h, 1, 0, 'PENDING', NOW())"
            ),
            {"id": str(unrelated_snap_id), "op_id": str(unrelated_op_id), "h": "0" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot_member "
                "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, locator, content_sha256) "
                "VALUES (:id, :s_id, 'ENDPOINT_OPERATION', :e_id, :o_id, NULL, '$.records', :h)"
            ),
            {
                "id": str(unrelated_member_id),
                "s_id": str(unrelated_snap_id),
                "e_id": str(unrelated_ep_id),
                "o_id": str(unrelated_op_id),
                "h": "0" * 64,
            },
        )

    # Scoped check ensures bootstrap of SYNTHETIC_DEV succeeds without false positive from UNRELATED_SOURCE's $.records
    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=embedding_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    assert summary.reused is False
    assert summary.knowledge_index_id is not None


async def test_bootstrap_embeds_all_100_records_without_gold_filter(database) -> None:
    from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
    from ai_worker.tasks.rag.evidence_search import SensitiveVector
    from ai_worker.tasks.rag.text_embedding import TextEmbeddingSuccess

    records = load_synthetic_knowledge_statements(SYNTHETIC_INDEX_PATH)

    class SpyEmbeddingAdapter:
        def __init__(self, dimension: int = 1536) -> None:
            self.dimension = dimension
            self.model_ref = "fake:deterministic-fake-embedding"
            self.model_version = "1.0.0"
            self._adapter_artifact_ref = ImmutableArtifactRef(
                artifact_code="deterministic-fake-embedding",
                version="1.0.0",
                content_sha256="0" * 64,
            )
            self.recorded_texts: list[str] = []

        async def embed(self, texts, *, model_ref=None, model_version=None, dimension=None):
            if isinstance(texts, (SensitiveText, str)):
                val = (
                    texts.reveal()
                    if hasattr(texts, "reveal")
                    else (texts.expose() if hasattr(texts, "expose") else str(texts))
                )
                self.recorded_texts.append(val)
                sha = hashlib.sha256(val.encode("utf-8")).digest()
                vec = tuple((b / 255.0) for b in (sha * 48)[: self.dimension])
                return TextEmbeddingSuccess(
                    embedding=SensitiveVector(vec),
                    adapter_artifact_ref=self._adapter_artifact_ref,
                )
            return ()

    spy = SpyEmbeddingAdapter()
    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=spy,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    assert summary.reused is False

    # Assert exactly 100 statements embedded unconditionally (without gold label filtering)
    assert len(spy.recorded_texts) == 100
    expected_statements = [r["statement"] for r in records]
    assert spy.recorded_texts == expected_statements


async def test_production_openai_adapter_composition_without_network(database) -> None:
    class MockResolved:
        repository_root = ROOT

    fake_client = MagicMock()
    mock_vec = [0.002] * 1536
    item = MagicMock()
    item.embedding = mock_vec
    item.index = 0
    resp = MagicMock()
    resp.data = [item]
    resp.model = "text-embedding-3-large"
    fake_client.embeddings.create = AsyncMock(return_value=resp)

    prod_adapter = OpenAITextEmbeddingAdapter(
        client=fake_client,
        adapter_artifact_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64),
    )

    # Assert prod_adapter identity matches normative contract
    assert prod_adapter.model_ref == "openai:text-embedding-3-large"
    assert prod_adapter.model_version == "text-embedding-3-large"
    assert prod_adapter.dimension == 1536

    summary = await bootstrap_dev_knowledge_index(
        database,
        text_embedding_port=prod_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    async with database.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT embedding_model_ref, embedding_model_version, embedding_dimension "
                        "FROM rag_knowledge_index WHERE id = :idx_id"
                    ),
                    {"idx_id": str(summary.knowledge_index_id)},
                )
            )
            .mappings()
            .one()
        )
    assert row["embedding_model_ref"] == "openai:text-embedding-3-large"
    assert row["embedding_model_version"] == "text-embedding-3-large"
    assert row["embedding_dimension"] == 1536

    capturing_search_port = CapturingSearchPort()
    registry = build_actual_adapter_registry(
        MockResolved(),
        engine=database,
        search_port=capturing_search_port,
        text_embedding_port=prod_adapter,
        synthetic_index_path=SYNTHETIC_INDEX_PATH,
    )
    adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
    assert adapter is not None

    req_hybrid = AdapterRequest(
        run_id=str(uuid4()),
        case=DummyCase("case-prod-hybrid", "아세트아미노펜 복용법"),
        task_type=TaskType.RETRIEVAL,
        input_sha256="0" * 64,
        case_resource_sha256="0" * 64,
        variant_id="RET-H",
        variant_manifest_hash="0" * 64,
    )
    res_hybrid = await adapter.execute(req_hybrid)
    assert res_hybrid.execution_status == "COMPLETED"

    # Verify that query embedding was requested with the exact production parameters
    assert fake_client.embeddings.create.call_count >= 1
    last_call = fake_client.embeddings.create.call_args
    assert last_call.kwargs["model"] == "text-embedding-3-large"
    assert last_call.kwargs["dimensions"] == 1536
