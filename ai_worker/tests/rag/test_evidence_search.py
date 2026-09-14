from __future__ import annotations

import uuid

import pytest

from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchFailure,
    EvidenceSearchFailureReason,
    EvidenceSearchRequest,
    QueryEmbeddingReceipt,
    SensitiveVector,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
    format_observed_score,
    validate_query_text,
    validate_search_request,
)


def test_port_isolation_and_naming() -> None:
    import ai_worker.tasks.rag.evidence_retrieval as ret_mod
    import ai_worker.tasks.rag.evidence_search as search_mod

    # Old module does NOT have EvidenceSearchPort
    assert not hasattr(ret_mod, "EvidenceSearchPort")
    assert hasattr(ret_mod, "SyntheticEvidenceSearchPort")

    # New module has async EvidenceSearchPort
    assert hasattr(search_mod, "EvidenceSearchPort")
    assert issubclass(search_mod.EvidenceSearchPort, object)


def test_sensitive_text_and_vector_redaction() -> None:
    text = SensitiveText("patient sensitive query text")
    assert repr(text) == "<redacted>"
    assert str(text) == "<redacted>"
    assert text.reveal() == "patient sensitive query text"

    vec = SensitiveVector((0.1, 0.2, 0.3))
    assert repr(vec) == "<redacted>"
    assert str(vec) == "<redacted>"
    assert vec.reveal() == (0.1, 0.2, 0.3)


def test_query_validation_length_and_whitespace() -> None:
    # Valid
    valid_query = SensitiveText("아스피린 복용법")
    validate_query_text(valid_query)

    # Empty
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText(""))

    # Whitespace only
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText("   "))

    # Leading whitespace
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText(" 아스피린"))

    # Trailing whitespace
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText("아스피린 "))

    # Too long (> 2000 code points)
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText("a" * 2001))

    # Exactly 2000 chars is valid
    validate_query_text(SensitiveText("a" * 2000))


def test_query_validation_forbidden_characters() -> None:
    # NUL
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText("아스피린\u0000"))

    # Zero-width spaces
    for zw in ("\u200b", "\u200c", "\u200d"):
        with pytest.raises(ValueError, match="REQUEST_INVALID"):
            validate_query_text(SensitiveText(f"아스피린{zw}"))

    # Bidi control characters
    for bidi in ("\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"):
        with pytest.raises(ValueError, match="REQUEST_INVALID"):
            validate_query_text(SensitiveText(f"아스피린{bidi}"))

    # BOM
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText("\ufeff아스피린"))

    # Non-NFC
    non_nfc = "\u1100\u1161"  # NFD for '가'
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        validate_query_text(SensitiveText(non_nfc))


def test_observed_score_formatting() -> None:
    # Exact hit is 1
    assert format_observed_score(1.0) == "1"

    # 0 is "0"
    assert format_observed_score(0.0) == "0"
    assert format_observed_score(-0.0) == "0"

    # 0.5 is exactly representable in binary float -> "0.5"
    assert format_observed_score(0.5) == "0.5"
    # 0.3 converted with Decimal.from_float quantized to 18 places is 0.299999999999999989
    assert format_observed_score(0.3) == "0.299999999999999989"

    # Very small number
    assert format_observed_score(0.000000000000000001) == "0.000000000000000001"

    # Rounding beyond 18 digits (ROUND_HALF_EVEN)
    # 0.0000000000000000014 -> 0.000000000000000001
    assert format_observed_score(0.0000000000000000014) == "0.000000000000000001"

    # Infinite and NaN must raise ValueError
    with pytest.raises(ValueError):
        format_observed_score(float("inf"))
    with pytest.raises(ValueError):
        format_observed_score(float("nan"))


def test_sensitive_vector_validation() -> None:
    # Valid
    vec = SensitiveVector((0.1, 0.2, 0.3))
    assert len(vec.reveal()) == 3

    # Zero vector norm rejected
    with pytest.raises(ValueError, match="zero vector"):
        SensitiveVector((0.0, 0.0, 0.0))

    # NaN / Inf rejected
    with pytest.raises(ValueError, match="finite"):
        SensitiveVector((0.1, float("nan")))
    with pytest.raises(ValueError, match="finite"):
        SensitiveVector((0.1, float("inf")))


def _valid_lexical_config() -> VersionedLexicalSearchConfiguration:
    return VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="lexical-search-config",
            version="1.0.0",
            content_sha256="0" * 64,  # will be computed in tests
        ),
        exact_strategy="case-sensitive-substring-v1",
        query_normalization="caller-supplied-nonblank-nfc-no-silent-transform-v1",
        trigram_match_operator="%",
        trigram_score_function="similarity",
        trigram_threshold="0.3",
        fts_regconfig="simple",
        fts_vector_expression="to_tsvector('simple', chunk_text)",
        fts_query_constructor="plainto_tsquery('simple', query)",
        fts_score_function="ts_rank_cd",
        exact_limit=20,
        trigram_limit=20,
        fts_limit=20,
    )


def test_versioned_retrieval_configuration_hashes() -> None:
    lex = _valid_lexical_config()
    lex_hash = lex.compute_canonical_hash()
    assert len(lex_hash) == 64

    # Bound lexical config with matching content_sha256
    lex_bound = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="lexical-search-config",
            version="1.0.0",
            content_sha256=lex_hash,
        ),
        exact_strategy=lex.exact_strategy,
        query_normalization=lex.query_normalization,
        trigram_match_operator=lex.trigram_match_operator,
        trigram_score_function=lex.trigram_score_function,
        trigram_threshold=lex.trigram_threshold,
        fts_regconfig=lex.fts_regconfig,
        fts_vector_expression=lex.fts_vector_expression,
        fts_query_constructor=lex.fts_query_constructor,
        fts_score_function=lex.fts_score_function,
        exact_limit=lex.exact_limit,
        trigram_limit=lex.trigram_limit,
        fts_limit=lex.fts_limit,
    )
    assert lex_bound.is_hash_valid()

    ret_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="evidence-retrieval-config",
            version="1.0.0",
            content_sha256="0" * 64,
        ),
        lexical_config=lex_bound,
        dense_config=None,
        expected_query_embedding_adapter_ref=None,
    )
    ret_hash = ret_config.compute_canonical_hash()
    assert len(ret_hash) == 64

    ret_bound = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="evidence-retrieval-config",
            version="1.0.0",
            content_sha256=ret_hash,
        ),
        lexical_config=lex_bound,
        dense_config=None,
        expected_query_embedding_adapter_ref=None,
    )
    assert ret_bound.is_hash_valid()


def test_request_validation_dense_active_vs_inactive() -> None:
    lex = _valid_lexical_config()
    lex_bound = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="lexical-search-config",
            version="1.0.0",
            content_sha256=lex.compute_canonical_hash(),
        ),
        exact_strategy=lex.exact_strategy,
        query_normalization=lex.query_normalization,
        trigram_match_operator=lex.trigram_match_operator,
        trigram_score_function=lex.trigram_score_function,
        trigram_threshold=lex.trigram_threshold,
        fts_regconfig=lex.fts_regconfig,
        fts_vector_expression=lex.fts_vector_expression,
        fts_query_constructor=lex.fts_query_constructor,
        fts_score_function=lex.fts_score_function,
        exact_limit=lex.exact_limit,
        trigram_limit=lex.trigram_limit,
        fts_limit=lex.fts_limit,
    )
    ret_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="evidence-retrieval-config",
            version="1.0.0",
            content_sha256="0" * 64,
        ),
        lexical_config=lex_bound,
        dense_config=None,
        expected_query_embedding_adapter_ref=None,
    )
    ret_bound = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="evidence-retrieval-config",
            version="1.0.0",
            content_sha256=ret_config.compute_canonical_hash(),
        ),
        lexical_config=lex_bound,
        dense_config=None,
        expected_query_embedding_adapter_ref=None,
    )

    binding = EvidenceSearchExecutionBinding(
        filter_snapshot_ref=ImmutableArtifactRef("filter", "1.0", "a" * 64),
        evidence_index_ref=ImmutableArtifactRef("index", "1.0", "b" * 64),
        knowledge_index_id=uuid.uuid4(),
        allowed_source_snapshot_ids=(uuid.uuid4(),),
        allowed_source_snapshot_member_ids=(uuid.uuid4(),),
        retrieval_config=ret_bound,
    )

    # 1. Dense inactive but receipt provided -> REQUEST_INVALID
    receipt = QueryEmbeddingReceipt(
        query_fingerprint=QueryFingerprint("sha256", "v1", "c" * 64),
        model_ref="test-model",
        model_version="1.0",
        dimension=3,
        embedding=SensitiveVector((0.1, 0.2, 0.3)),
        adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "d" * 64),
    )
    req_with_unexpected_receipt = EvidenceSearchRequest(
        normalized_query=SensitiveText("아스피린"),
        query_fingerprint=QueryFingerprint("sha256", "v1", "c" * 64),
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )
    res = validate_search_request(req_with_unexpected_receipt)
    assert isinstance(res, EvidenceSearchFailure)
    assert res.reason == EvidenceSearchFailureReason.REQUEST_INVALID

    # 2. Dense inactive and receipt is None -> OK
    req_dense_inactive = EvidenceSearchRequest(
        normalized_query=SensitiveText("아스피린"),
        query_fingerprint=QueryFingerprint("sha256", "v1", "c" * 64),
        execution_binding=binding,
        query_embedding_receipt=None,
    )
    res2 = validate_search_request(req_dense_inactive)
    assert res2 is None
