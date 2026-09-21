"""Unit tests for the RET-H AWS synthetic smoke composition root."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest

from ai_worker.tasks.evaluation.ret_h_smoke import (
    LOCATOR_MISMATCH_SUFFIX,
    STALE_SOURCE_VERSION_SUFFIX,
    build_receipt_verifier,
    build_text_embedding_port,
    make_locator_mismatch_hit,
    make_stale_hit,
    verify_gate_fail_closed,
)
from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt
from ai_worker.tasks.rag.evidence_search import (
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateReason as Reason,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateStatus,
    PostSearchEligibilityFailure,
    PostSearchEligibilitySuccess,
)
from ai_worker.tasks.rag.retrieval_run import PersistedRetrievalRunReceipt, compute_receipt_hash

INDEX_ID = uuid.UUID("17810000-0000-4000-8000-0000000000aa")
CHUNK_ID = uuid.UUID("17810000-0000-4000-8000-0000000000bb")

SIGNAL_MANIFEST_HASH = "1" * 64
HIT_MANIFEST_HASH = "2" * 64
SEARCH_RECEIPT_HASH = "3" * 64


def _hit() -> ProductionSearchHit:
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=INDEX_ID,
        index_code="rag-ret-h-aws-synthetic-smoke",
        index_version="1.0.0",
        index_configuration_hash="a" * 64,
        knowledge_chunk_id=CHUNK_ID,
        evidence_key="synthetic-ret-h-smoke-evidence",
        source_snapshot_id=uuid.uuid4(),
        source_snapshot_member_id=uuid.uuid4(),
        source_code="SYNTHETIC_DEV",
        source_version="2026-09-17T00:00:00Z",
        canonical_checksum="b" * 64,
        external_document_id="synthetic-doc-1",
        chunk_index=0,
        locator="$['records'][0]",
        content_hash="c" * 64,
        canonicalization_spec_version="canonical-v1",
        normalization_version="normalize-v1",
    )
    return ProductionSearchHit(
        provenance=provenance,
        coordinate=StableCoordinate(
            source_code=provenance.source_code,
            source_version=provenance.source_version,
            external_document_id=provenance.external_document_id,
            chunk_index=provenance.chunk_index,
        ),
        exact_hit=True,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=1,
        dense_rank=1,
        fusion_rank=1,
        fraction_receipt=FractionReceipt("1", "61"),
        is_eligible_for_future_reranker=True,
    )


def _receipt(**overrides: Any) -> PersistedRetrievalRunReceipt:
    base: dict[str, Any] = {
        "run_id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "node_id": "hybrid_retrieve",
        "variant": "RET-H",
        "status": "COMPLETED",
        "query_digest": "d" * 64,
        "retrieval_configuration_hash": "e" * 64,
        "source_manifest_hash": "f" * 64,
        "receipt_hash": "",
        "total_signals": 4,
        "total_hits": 3,
        "selected_count": 1,
        "signal_manifest_hash": SIGNAL_MANIFEST_HASH,
        "hit_manifest_hash": HIT_MANIFEST_HASH,
        "search_receipt_hash": SEARCH_RECEIPT_HASH,
    }
    base.update(overrides)
    receipt = PersistedRetrievalRunReceipt(**base)
    if not receipt.receipt_hash:
        receipt = replace(
            receipt,
            receipt_hash=compute_receipt_hash(
                run_id=receipt.run_id,
                job_id=receipt.job_id,
                node_id=receipt.node_id,
                variant=receipt.variant,
                status=receipt.status,
                query_digest=receipt.query_digest,
                retrieval_configuration_hash=receipt.retrieval_configuration_hash,
                source_manifest_hash=receipt.source_manifest_hash,
                search_receipt_hash=receipt.search_receipt_hash,
                total_signals=receipt.total_signals,
                total_hits=receipt.total_hits,
                selected_count=receipt.selected_count,
                signal_manifest_hash=receipt.signal_manifest_hash,
                hit_manifest_hash=receipt.hit_manifest_hash,
            ),
        )
    return receipt


def _verifier() -> Any:
    return build_receipt_verifier()


def test_receipt_verifier_reuses_the_canonical_hash_domain() -> None:
    assert _verifier()(_receipt()) is True


def test_receipt_verifier_rejects_a_tampered_receipt_hash() -> None:
    assert _verifier()(replace(_receipt(), receipt_hash="9" * 64)) is False


def test_receipt_verifier_rejects_a_tampered_row_count() -> None:
    assert _verifier()(replace(_receipt(), selected_count=2)) is False


def test_receipt_verifier_rejects_a_tampered_manifest_hash() -> None:
    assert _verifier()(replace(_receipt(), hit_manifest_hash="7" * 64)) is False


def test_stale_and_locator_transforms_only_touch_in_memory_provenance() -> None:
    original = _hit()
    stale = make_stale_hit(original)
    mismatch = make_locator_mismatch_hit(original)

    assert stale.provenance.source_version.endswith(STALE_SOURCE_VERSION_SUFFIX)
    assert stale.provenance.locator == original.provenance.locator
    assert mismatch.provenance.locator.endswith(LOCATOR_MISMATCH_SUFFIX)
    assert mismatch.provenance.source_version == original.provenance.source_version
    # The original candidate is untouched; nothing is written anywhere.
    assert original.provenance.source_version == "2026-09-17T00:00:00Z"


class _Verifier:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def pre_search(self, request: Any) -> Any:  # pragma: no cover - unused here
        raise NotImplementedError

    async def post_search(self, request: Any, hits: Any) -> Any:
        return self._outcome


async def test_gate_is_fail_closed_when_the_verifier_drops_the_tampered_chunk() -> None:
    verifier = _Verifier(PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset()))
    fail_closed, _ = await verify_gate_fail_closed(
        eligibility_verifier=verifier,
        knowledge_index_id=INDEX_ID,
        tampered_hits=(make_stale_hit(_hit()),),
    )
    assert fail_closed is True


async def test_gate_fail_open_is_detected_when_a_tampered_chunk_stays_eligible() -> None:
    verifier = _Verifier(PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset({CHUNK_ID})))
    fail_closed, message = await verify_gate_fail_closed(
        eligibility_verifier=verifier,
        knowledge_index_id=INDEX_ID,
        tampered_hits=(make_locator_mismatch_hit(_hit()),),
    )
    assert fail_closed is False
    assert "selected a tampered candidate" in message


async def test_verifier_level_failure_counts_as_fail_closed() -> None:
    verifier = _Verifier(
        PostSearchEligibilityFailure(
            status=EvidenceGateStatus.VALIDATION_ERROR,
            reason=Reason.STALE,
            message="stale provenance",
        )
    )
    fail_closed, _ = await verify_gate_fail_closed(
        eligibility_verifier=verifier,
        knowledge_index_id=INDEX_ID,
        tampered_hits=(make_stale_hit(_hit()),),
    )
    assert fail_closed is True


async def test_no_candidate_is_never_a_fail_closed_pass() -> None:
    verifier = _Verifier(PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset()))
    fail_closed, message = await verify_gate_fail_closed(
        eligibility_verifier=verifier,
        knowledge_index_id=INDEX_ID,
        tampered_hits=(),
    )
    assert fail_closed is False
    assert "no tampered candidate" in message


@pytest.mark.parametrize("environment", [{}, {"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": "   "}])
def test_missing_credential_never_falls_back_to_a_fake_embedding(environment: dict[str, str]) -> None:
    assert build_text_embedding_port(content_sha256="0" * 64, environment=environment) is None


def test_build_search_port_uses_canonical_candidate_ref() -> None:
    from unittest.mock import MagicMock

    from ai_worker.adapters.postgresql_evidence_search import POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF
    from ai_worker.tasks.evaluation.ret_h_smoke import build_search_port

    fake_factory = MagicMock()
    port1 = build_search_port(fake_factory)
    assert port1._adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF

    port2 = build_search_port(fake_factory, content_sha256=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.content_sha256)
    assert port2._adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF


def test_build_search_port_rejects_arbitrary_hash_fail_closed() -> None:
    from unittest.mock import MagicMock

    from ai_worker.tasks.evaluation.ret_h_smoke import build_search_port

    fake_factory = MagicMock()
    with pytest.raises(ValueError, match="Search adapter hash mismatch"):
        build_search_port(fake_factory, content_sha256="a" * 64)

    with pytest.raises(ValueError, match="Search adapter hash mismatch"):
        build_search_port(fake_factory, content_sha256="5" * 64)
