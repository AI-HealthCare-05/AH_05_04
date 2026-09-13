import hashlib
from dataclasses import replace
from uuid import UUID

import pytest

from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
    canonical_corpus_manifest_hash,
    canonical_embedding_sha256,
    create_knowledge_index_receipt,
)


def member_draft(
    *,
    locator: str = "$.records[0]",
    embedding: tuple[float, ...] = (1.0, 0.0),
) -> KnowledgeIndexMemberDraft:
    text = "합성 의약품 근거"
    return KnowledgeIndexMemberDraft(
        identity=KnowledgeChunkIdentity(
            knowledge_chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
            source_snapshot_id=UUID("00000000-0000-4000-8000-000000000002"),
            source_snapshot_member_id=UUID("00000000-0000-4000-8000-000000000003"),
            source_code="MFDS",
            source_version="external:v1",
            canonical_checksum="a" * 64,
            external_document_id="doc-1",
            chunk_index=0,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            locator=locator,
        ),
        content_text=SensitiveEvidenceText(text),
        embedding=embedding,
    )


def build_request(*members: KnowledgeIndexMemberDraft) -> KnowledgeIndexBuildRequest:
    return KnowledgeIndexBuildRequest(
        index_code="knowledge-evidence",
        index_version="2026-09-13.1",
        embedding_model_ref="synthetic-embedding",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=tuple(members),
    )


def test_corpus_manifest_has_hand_checked_golden_hash() -> None:
    member = replace(member_draft().identity, content_hash="1" * 64)

    assert canonical_corpus_manifest_hash((member,)) == (
        "886fce89b003343646fb319118c75f89bbcf66f4771cbd283c777a44d421a806"
    )


def test_corpus_manifest_ignores_locator_and_embedding() -> None:
    first = member_draft(locator="$.records[0]", embedding=(1.0, 0.0))
    second = member_draft(locator="$.records[1]", embedding=(0.0, 1.0))

    assert canonical_corpus_manifest_hash((first.identity,)) == canonical_corpus_manifest_hash((second.identity,))


def test_locator_allows_internal_spaces_used_by_source_member_contract() -> None:
    member = member_draft(locator="$.records[0] item")

    assert member.identity.locator == "$.records[0] item"


def test_embedding_hash_has_hand_checked_golden_hash() -> None:
    assert canonical_embedding_sha256((1.0, 0.0)) == (
        "6d55b3f0997762a9f412cd80a4e4f0b28b5a2d51b70088123d6ea4a1bfa8dfa9"
    )


@pytest.mark.parametrize(
    "embedding",
    [
        (-0.0, 1.0),
        (0.0, 0.0),
        (1e-46, 0.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
    ],
)
def test_embedding_hash_rejects_noncanonical_vectors(embedding: tuple[float, ...]) -> None:
    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        canonical_embedding_sha256(embedding)

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID
    assert repr(exc_info.value) == "KnowledgeEvidenceIndexValidationError(EMBEDDING_INVALID)"


def test_sensitive_evidence_text_redacts_ordinary_representations() -> None:
    value = SensitiveEvidenceText("SECRET_SENTINEL")

    assert str(value) == "<redacted>"
    assert repr(value) == "<redacted>"
    assert value.reveal() == "SECRET_SENTINEL"


def test_index_request_representations_hide_locator_text_and_embedding() -> None:
    member = member_draft(locator="SENSITIVE_LOCATOR_178", embedding=(0.25, 0.75))
    request = build_request(member)

    for value in (member.identity, member, request):
        representation = repr(value)
        assert "SENSITIVE_LOCATOR_178" not in representation
        assert "합성 의약품 근거" not in representation
        assert "(0.25, 0.75)" not in representation


def test_member_rejects_content_hash_mismatch() -> None:
    member = member_draft()

    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        replace(member, identity=replace(member.identity, content_hash="f" * 64))

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.CONTENT_HASH_MISMATCH


class CapturingRepository:
    def __init__(self, *, mutate_receipt: bool = False) -> None:
        self.mutate_receipt = mutate_receipt
        self.request: KnowledgeIndexBuildRequest | None = None

    async def persist_complete_index(self, request, receipt):
        self.request = request
        if self.mutate_receipt:
            return replace(receipt, member_count=receipt.member_count + 1)
        return receipt


@pytest.mark.asyncio
async def test_build_service_returns_exact_repository_receipt() -> None:
    request = build_request(member_draft())
    repository = CapturingRepository()

    result = await build_knowledge_evidence_index(request, repository=repository)

    assert result == create_knowledge_index_receipt(request)
    assert repository.request == request


@pytest.mark.asyncio
async def test_build_service_rejects_changed_repository_receipt() -> None:
    request = build_request(member_draft())

    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        await build_knowledge_evidence_index(request, repository=CapturingRepository(mutate_receipt=True))

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH
