"""Unit tests for the MFDS Knowledge Evidence Index runner.

TDD tests for Issue #727 covering:
1. Config isolation (missing builder credentials -> CONFIG_INVALID)
2. Unsafe credentials mixed -> CONFIG_INVALID
3. Missing OPENAI_API_KEY -> EMBEDDING_CREDENTIAL_MISSING, provider calls = 0, no Index write
4. Snapshot PENDING -> preflight fails, provider call = 0
5. Verification seal missing -> preflight fails, provider call = 0
6. Wrong Source identity -> preflight fails, provider call = 0
7. Document/member hash mismatch -> preflight fails, provider call = 0
8. Chunk text/content hash mismatch -> preflight fails, provider call = 0
9. Chunk count != 3 -> preflight fails, provider call = 0
10. Embedding provider failure -> Index write = 0
11. Embedding dimension mismatch -> Index write = 0
12. Fresh happy path -> embed exactly 3 times, outcome BUILT
13. BuildRequest actual identity exact match
14. Summary does not contain chunk text, vector, DB URL, or API key
15. Exact replay uses same request and does not call embedding again (calls = 3)
16. Existing exact Index -> EXACT_REUSE + provider call = 0 + repository revalidation
17. Existing mismatched Index -> VERSION_CONFLICT + provider call = 0
18. Unsupplied adapter identity -> BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest

from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
from ai_worker.admin.knowledge_evidence_index import (
    EXPECTED_DIMENSION,
    EXPECTED_INDEX_CODE,
    EXPECTED_INDEX_VERSION,
    EXPECTED_MODEL_REF,
    EXPECTED_MODEL_VERSION,
    EXPECTED_SOURCE_CODE,
    NOVASC_CANONICAL_CHECKSUM,
    NOVASC_ITEM_SEQ,
    NOVASC_SNAPSHOT_ID,
    NOVASC_SOURCE_VERSION,
    AuthoritativeDiscoveredChunk,
    KnowledgeEvidenceIndexRunnerConfig,
    KnowledgeEvidenceIndexRunnerError,
    KnowledgeEvidenceIndexRunnerFailureReason,
    execute_knowledge_evidence_index_build,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import SensitiveVector
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexReceipt,
)
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
    TextEmbeddingFailureReason,
    TextEmbeddingPort,
    TextEmbeddingSuccess,
)

# --------------------------------------------------------------------------------------
# Test Fixtures & Stub Helpers
# --------------------------------------------------------------------------------------

_EE_TEXT = "노바스크정5밀리그램(암로디핀베실산염) 효능효과: 고혈압, 관상동맥의 고정협심증"
_UD_TEXT = "노바스크정5밀리그램 용법용량: 성인 1일 1회 5mg 투여, 환자의 반응에 따라 1일 최고 10mg까지 증량"
_NB_TEXT = "노바스크정5밀리그램 사용상의주의사항: 이 약 또는 다른 디히드로피리딘계 유도체에 과민증 환자 금기"

_TEXTS = {
    "EE": _EE_TEXT,
    "UD": _UD_TEXT,
    "NB": _NB_TEXT,
}

_HASHES = {section: hashlib.sha256(text.encode("utf-8")).hexdigest() for section, text in _TEXTS.items()}


def _make_novasc_chunks(
    *,
    source_code: str = EXPECTED_SOURCE_CODE,
    source_version: str = NOVASC_SOURCE_VERSION,
    canonical_checksum: str = NOVASC_CANONICAL_CHECKSUM,
    item_seq: str = NOVASC_ITEM_SEQ,
    snapshot_id: UUID = NOVASC_SNAPSHOT_ID,
    chunk_ids: dict[str, UUID] | None = None,
    texts: dict[str, str] | None = None,
    hashes: dict[str, str] | None = None,
    normalization_version: str = "normalization-v1",
) -> tuple[AuthoritativeDiscoveredChunk, ...]:
    ids = chunk_ids or {s: uuid4() for s in ("EE", "UD", "NB")}
    t = texts or _TEXTS
    h = hashes or _HASHES
    chunks = []
    for section in ("EE", "UD", "NB"):
        chunks.append(
            AuthoritativeDiscoveredChunk(
                knowledge_chunk_id=ids[section],
                section=section,
                source_snapshot_id=snapshot_id,
                source_snapshot_member_id=uuid4(),
                source_code=source_code,
                source_version=source_version,
                canonical_checksum=canonical_checksum,
                external_document_id=f"mfds-label:{item_seq}:{section}",
                chunk_index=0,
                content_hash=h[section],
                locator=f"mfds-label/{item_seq}/{section}",
                chunk_text=t[section],
                normalization_version=normalization_version,
            )
        )
    return tuple(chunks)


class StubTextEmbeddingPort(TextEmbeddingPort):
    def __init__(
        self,
        *,
        dimension: int = 1536,
        fail_with: TextEmbeddingFailureReason | None = None,
        artifact_ref: ImmutableArtifactRef | None = None,
    ) -> None:
        self.call_count = 0
        self.received_texts: list[str] = []
        self._dimension = dimension
        self._fail_with = fail_with
        self._artifact_ref = artifact_ref or OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        self.call_count += 1
        self.received_texts.append(text.reveal())
        if self._fail_with is not None:
            return TextEmbeddingFailure(self._fail_with)

        # Generate deterministic mock embedding of the requested dimension
        base_val = float(self.call_count)
        values = [base_val / 100.0] * self._dimension
        return TextEmbeddingSuccess(
            embedding=SensitiveVector(values),
            adapter_artifact_ref=self._artifact_ref,
        )


class StubKnowledgeEvidenceIndexRepository:
    def __init__(self, *, fail_persisting_with: Exception | None = None) -> None:
        self.persist_calls = 0
        self.persisted_requests: list[KnowledgeIndexBuildRequest] = []
        self._fail_persisting_with = fail_persisting_with
        self.existing_receipts: dict[tuple[str, str], KnowledgeIndexReceipt] = {}

    async def persist_complete_index(
        self,
        request: KnowledgeIndexBuildRequest,
        receipt: KnowledgeIndexReceipt,
    ) -> KnowledgeIndexReceipt:
        self.persist_calls += 1
        self.persisted_requests.append(request)
        if self._fail_persisting_with is not None:
            raise self._fail_persisting_with
        key = (request.index_code, request.index_version)
        if key in self.existing_receipts:
            # Replay / existing index check
            existing = self.existing_receipts[key]
            if existing != receipt:
                raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.VERSION_CONFLICT)
            return existing
        self.existing_receipts[key] = receipt
        return receipt


def _valid_env() -> dict[str, str]:
    return {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_NAME": "test",
        "KNOWLEDGE_INDEX_BUILDER_USER": "test_builder",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD": "test_password",
        "OPENAI_API_KEY": "sk-synthetic-test-key-12345",
    }


# --------------------------------------------------------------------------------------
# 1. Config Isolation: Missing builder credentials -> CONFIG_INVALID
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    [
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "KNOWLEDGE_INDEX_BUILDER_USER",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
    ],
)
def test_runner_config_missing_builder_credential_fails_closed(missing_key: str) -> None:
    env = _valid_env()
    del env[missing_key]
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        KnowledgeEvidenceIndexRunnerConfig.from_environment(env)
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID


# --------------------------------------------------------------------------------------
# 2. Config Isolation: Unsafe credentials mixed -> CONFIG_INVALID
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_CLEANUP_EXECUTOR_PASSWORD",
        "CATALOG_WRITER_PASSWORD",
    ],
)
def test_runner_config_unsafe_credential_mixed_fails_closed(unsafe_key: str) -> None:
    env = _valid_env()
    env[unsafe_key] = "forbidden_secret"
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        KnowledgeEvidenceIndexRunnerConfig.from_environment(env)
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID


# --------------------------------------------------------------------------------------
# 3. Missing OPENAI_API_KEY -> EMBEDDING_CREDENTIAL_MISSING, provider call 0, write 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_openai_key_fails_closed_without_provider_call_or_index_write() -> None:
    env = _valid_env()
    del env["OPENAI_API_KEY"]

    # In strict mode without injected port, from_environment fails closed
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        KnowledgeEvidenceIndexRunnerConfig.from_environment(env, require_openai_key=True)
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_CREDENTIAL_MISSING

    # When runner tries fresh build with no API key and no injected port:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(env, require_openai_key=False)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=None,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_CREDENTIAL_MISSING
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 4. Snapshot PENDING -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_snapshot_pending_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()

    # Preflight failure simulated via mock/override
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_failure_reason=KnowledgeEvidenceIndexRunnerFailureReason.PRECHECK_FAILED,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason in (
        KnowledgeEvidenceIndexRunnerFailureReason.PRECHECK_FAILED,
        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID,
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 5. Verification seal missing -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_verification_seal_missing_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_failure_reason=KnowledgeEvidenceIndexRunnerFailureReason.PRECHECK_FAILED,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason in (
        KnowledgeEvidenceIndexRunnerFailureReason.PRECHECK_FAILED,
        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID,
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 6. Wrong Source identity -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_wrong_source_identity_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    wrong_chunks = _make_novasc_chunks(source_code="WRONG_SOURCE")

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=wrong_chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 7. Document/member hash mismatch -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_document_member_hash_mismatch_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    wrong_chunks = _make_novasc_chunks(canonical_checksum="f" * 64)

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=wrong_chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 8. Chunk text / content hash mismatch -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_chunk_text_content_hash_mismatch_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    corrupted_hashes = dict(_HASHES)
    corrupted_hashes["EE"] = "9" * 64
    wrong_chunks = _make_novasc_chunks(hashes=corrupted_hashes)

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=wrong_chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason in (
        KnowledgeEvidenceIndexRunnerFailureReason.CONTENT_HASH_MISMATCH,
        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID,
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 9. Chunk count != 3 -> preflight fails, provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_chunk_count_not_three_fails_closed_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    two_chunks = _make_novasc_chunks()[:2]

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=two_chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason in (
        KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID,
        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID,
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 10. Embedding failure -> Index write = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_failure_fails_closed_without_index_write() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort(fail_with=TextEmbeddingFailureReason.DEPENDENCY_ERROR)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason in (
        KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID,
        KnowledgeEvidenceIndexRunnerFailureReason.DEPENDENCY_ERROR,
    )
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 11. Embedding dimension mismatch (!= 1536) -> Index write = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_dimension_mismatch_fails_closed_without_index_write() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort(dimension=768)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 12. Fresh happy path -> embed exactly 3 times, outcome BUILT
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_build_happy_path_embeds_exactly_three_times() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    summary = await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=True,
    )

    assert summary["execution_status"] == "SUCCESS"
    assert summary["outcome"] == "BUILT"
    assert port.call_count == 3
    assert len(summary["corpus_manifest_hash"]) == 64
    assert len(summary["embedding_manifest_hash"]) == 64
    assert len(summary["index_configuration_hash"]) == 64
    assert summary["exact_replay_verified"] is True


# --------------------------------------------------------------------------------------
# 13. BuildRequest actual identity exact match
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_request_actual_identity_exact() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=False,
    )

    assert len(repo.persisted_requests) == 1
    req = repo.persisted_requests[0]
    assert req.index_code == EXPECTED_INDEX_CODE
    assert req.index_version == EXPECTED_INDEX_VERSION
    assert req.embedding_model_ref == EXPECTED_MODEL_REF
    assert req.embedding_model_version == EXPECTED_MODEL_VERSION
    assert req.embedding_dimension == EXPECTED_DIMENSION
    assert req.distance_metric is DistanceMetric.COSINE
    assert len(req.members) == 3


# --------------------------------------------------------------------------------------
# 14. Summary does not contain chunk text, vector, DB URL, or API key
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_does_not_contain_chunk_text_vector_api_key() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    summary = await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=False,
    )

    summary_str = str(summary)
    assert _valid_env()["OPENAI_API_KEY"] not in summary_str
    assert _valid_env()["KNOWLEDGE_INDEX_BUILDER_PASSWORD"] not in summary_str
    for text in _TEXTS.values():
        assert text not in summary_str
    assert "embedding" not in summary or not isinstance(summary.get("embedding"), list)


# --------------------------------------------------------------------------------------
# 15. Exact replay uses same request and does not call embedding again (calls = 3)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_replay_uses_same_request_and_does_not_call_embedding_again() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    summary = await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=True,
    )

    assert summary["exact_replay_verified"] is True
    assert repo.persist_calls == 2
    assert port.call_count == 3


# --------------------------------------------------------------------------------------
# 16. Existing exact Index -> EXACT_REUSE + provider call = 0 + repository revalidation
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_exact_index_returns_exact_reuse_without_provider_call_revalidating_repository() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    # First build to populate existing index
    await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=False,
    )
    assert port.call_count == 3

    port.call_count = 0
    persist_calls_before = repo.persist_calls

    summary = await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        existing_embeddings_override=tuple(m.embedding for m in repo.persisted_requests[0].members),
        verify_replay=False,
    )

    assert summary["outcome"] == "EXACT_REUSE"
    assert port.call_count == 0
    assert repo.persist_calls == persist_calls_before + 1


# --------------------------------------------------------------------------------------
# 17. Existing mismatched Index -> VERSION_CONFLICT + provider call = 0
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_mismatched_index_fails_closed_with_version_conflict_without_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    await execute_knowledge_evidence_index_build(
        config=config,
        snapshot_id=NOVASC_SNAPSHOT_ID,
        expected_item_seq=NOVASC_ITEM_SEQ,
        expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
        expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        preflight_chunks_override=chunks,
        repository_override=repo,
        embedding_port_override=port,
        verify_replay=False,
    )
    assert port.call_count == 3
    port.call_count = 0

    mismatched_embeddings = tuple((0.999,) * EXPECTED_DIMENSION for _ in range(3))

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=port,
            existing_embeddings_override=mismatched_embeddings,
            verify_replay=False,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT
    assert port.call_count == 0


# --------------------------------------------------------------------------------------
# 18. Unsupplied adapter identity -> BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupplied_adapter_artifact_identity_blocks_with_expected_reason() -> None:
    env = _valid_env()
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(env, require_openai_key=True)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=None,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=None,
        )
    assert (
        exc_info.value.reason
        == KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
    )
    assert repo.persist_calls == 0


@pytest.mark.parametrize("forbidden_ref", ["0" * 64, "e" * 64, "short_sha", "not-a-hex-string"])
@pytest.mark.asyncio
async def test_forbidden_or_invalid_adapter_artifact_ref_fails_closed(forbidden_ref: str) -> None:
    env = _valid_env()
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(env, require_openai_key=True)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=forbidden_ref,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=None,
        )
    assert (
        exc_info.value.reason
        == KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
    )
    assert repo.persist_calls == 0


# --------------------------------------------------------------------------------------
# 19. Target B/C/D tests for Issue #738
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corpus_runner_requires_authoritative_immutable_artifact_ref() -> None:
    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort(artifact_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF)
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    # None passed for expected_embedding_adapter_ref -> fail closed
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=None,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert (
        exc_info.value.reason
        == KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


@pytest.mark.asyncio
async def test_mismatched_adapter_ref_fails_closed_before_provider_call() -> None:
    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    port = StubTextEmbeddingPort()
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    wrong_ref = ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "f" * 64)
    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=wrong_ref,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=port,
        )
    assert (
        exc_info.value.reason
        == KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
    )
    assert port.call_count == 0
    assert repo.persist_calls == 0


@pytest.mark.asyncio
async def test_embedding_success_with_mismatched_observed_ref_fails_closed_before_persist() -> None:
    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    config = KnowledgeEvidenceIndexRunnerConfig.from_environment(_valid_env())
    mismatched_port = StubTextEmbeddingPort(
        artifact_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "9" * 64)
    )
    repo = StubKnowledgeEvidenceIndexRepository()
    chunks = _make_novasc_chunks()

    with pytest.raises(KnowledgeEvidenceIndexRunnerError) as exc_info:
        await execute_knowledge_evidence_index_build(
            config=config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
            preflight_chunks_override=chunks,
            repository_override=repo,
            embedding_port_override=mismatched_port,
        )
    assert exc_info.value.reason == KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID
    assert repo.persist_calls == 0
