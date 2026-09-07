"""Synthetic-only adapters for the provisional Knowledge Evidence kernel."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Self

from ai_worker.tasks.rag.evidence_retrieval import (
    RERANK_INPUT_PROJECTION_VERSION,
    CanonicalScore,
    EvidenceRerankFailure,
    EvidenceRerankRequest,
    EvidenceRerankSelection,
    EvidenceRerankSuccess,
    EvidenceRetrievalKernelRequest,
    EvidenceSearchFailure,
    EvidenceSearchStage,
    EvidenceSearchSuccess,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    KnowledgeEvidenceSearchHit,
    QueryFingerprint,
    SensitiveText,
    StageSignal,
    canonical_rerank_input_hash,
)

_SCORE_PLACES = Decimal("0.000001")
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_SCORE_RE = re.compile(r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$")
_MATCHING_NORMALIZATION_STRATEGY = "unicode-nfc-casefold-collapse-whitespace-v1"
_TRIGRAM_STRATEGY = "synthetic-trigram-jaccard-v1"
_LEXICAL_ORDERING = "exact-first-then-score-desc-then-evidence-key-utf8-ascending"
_DENSE_ORDERING = "score-desc-then-evidence-key-utf8-ascending"


@dataclass(frozen=True, slots=True)
class SyntheticEvidenceRecord:
    evidence_key: str
    knowledge_chunk_ref: str
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    canonicalization_spec_version: str
    content_text: SensitiveText
    dense_vector: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyntheticEvidenceIndex:
    artifact_ref: ImmutableArtifactRef
    records: tuple[SyntheticEvidenceRecord, ...]

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        records: tuple[SyntheticEvidenceRecord, ...],
    ) -> Self:
        payload = {
            "records": [
                {
                    "evidence_key": record.evidence_key,
                    "knowledge_chunk_ref": record.knowledge_chunk_ref,
                    "source_snapshot_ref": _artifact_dict(record.source_snapshot_ref),
                    "source_version": record.source_version,
                    "locator": record.locator,
                    "content_sha256": _source_content_sha256(record.content_text),
                    "canonicalization_spec_version": record.canonicalization_spec_version,
                    "dense_vector": list(record.dense_vector),
                }
                for record in sorted(records, key=lambda item: item.evidence_key.encode())
            ]
        }
        return cls(_artifact_ref(artifact_code, version, payload), records)


@dataclass(frozen=True, slots=True)
class VersionedLexicalSearchConfig:
    artifact_ref: ImmutableArtifactRef
    trigram_similarity_threshold: str

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        trigram_similarity_threshold: str,
    ) -> Self:
        payload = {
            "exact_strategy": "normalized-substring-v1",
            "matching_normalization_strategy": _MATCHING_NORMALIZATION_STRATEGY,
            "trigram_strategy": _TRIGRAM_STRATEGY,
            "trigram_similarity_threshold": trigram_similarity_threshold,
            "ordering": _LEXICAL_ORDERING,
            "score_places": 6,
            "decimal_context": _decimal_context_payload(),
        }
        return cls(
            _artifact_ref(artifact_code, version, payload),
            trigram_similarity_threshold,
        )


@dataclass(frozen=True, slots=True)
class SyntheticDenseQueryVector:
    query_fingerprint: QueryFingerprint
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionedDenseSearchConfig:
    artifact_ref: ImmutableArtifactRef
    query_vectors: tuple[SyntheticDenseQueryVector, ...]
    minimum_similarity: str

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        query_vectors: tuple[SyntheticDenseQueryVector, ...],
        minimum_similarity: str,
    ) -> Self:
        payload = {
            "metric": "cosine-similarity-v1",
            "query_vectors": [
                {
                    "query_fingerprint": {
                        "algorithm": item.query_fingerprint.algorithm,
                        "key_version": item.query_fingerprint.key_version,
                        "digest": item.query_fingerprint.digest,
                    },
                    "values": list(item.values),
                }
                for item in sorted(query_vectors, key=_query_vector_sort_key)
            ],
            "minimum_similarity": minimum_similarity,
            "ordering": _DENSE_ORDERING,
            "score_places": 6,
            "decimal_context": _decimal_context_payload(),
        }
        return cls(
            _artifact_ref(artifact_code, version, payload),
            query_vectors,
            minimum_similarity,
        )


@dataclass(frozen=True, slots=True)
class VersionedRerankConfig:
    artifact_ref: ImmutableArtifactRef
    lexical_weight: str
    dense_weight: str
    top_k: int

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        lexical_weight: str,
        dense_weight: str,
        top_k: int,
    ) -> Self:
        payload = {
            "strategy": "weighted-stage-score-v1",
            "lexical_weight": lexical_weight,
            "dense_weight": dense_weight,
            "top_k": top_k,
            "tie_break": "evidence-key-utf8-ascending",
            "score_places": 6,
            "decimal_context": _decimal_context_payload(),
        }
        return cls(
            _artifact_ref(artifact_code, version, payload),
            lexical_weight,
            dense_weight,
            top_k,
        )


@dataclass(frozen=True, slots=True)
class VersionedEvidenceRerankAdapter:
    config: VersionedRerankConfig
    adapter_artifact_ref: ImmutableArtifactRef

    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess | EvidenceRerankFailure:
        try:
            if (
                request.rerank_config_ref != self.config.artifact_ref
                or not _valid_rerank_config(self.config)
                or not _rerank_config_is_bound(self.config)
                or request.projection_version != RERANK_INPUT_PROJECTION_VERSION
                or request.input_set_hash != canonical_rerank_input_hash(request.projection_version, request.candidates)
                or not _valid_rerank_candidates(request)
                or not _is_synthetic_artifact_ref(self.adapter_artifact_ref)
            ):
                return EvidenceRerankFailure()
            with localcontext(_DECIMAL_CONTEXT):
                lexical_weight = Decimal(self.config.lexical_weight)
                dense_weight = Decimal(self.config.dense_weight)
                if (
                    not lexical_weight.is_finite()
                    or not dense_weight.is_finite()
                    or lexical_weight < 0
                    or dense_weight < 0
                    or lexical_weight + dense_weight != 1
                    or isinstance(self.config.top_k, bool)
                    or self.config.top_k <= 0
                ):
                    return EvidenceRerankFailure()
                ranked = [
                    (
                        _weighted_score(candidate.stage_signals, lexical_weight, dense_weight),
                        candidate.provenance.evidence_key,
                    )
                    for candidate in request.candidates
                ]
            with localcontext(_DECIMAL_CONTEXT):
                ranked.sort(key=lambda item: (-item[0], item[1].encode()))
            selections = tuple(
                EvidenceRerankSelection(evidence_key, rank, CanonicalScore(_canonical_decimal(score)))
                for rank, (score, evidence_key) in enumerate(ranked[: self.config.top_k], start=1)
            )
            return EvidenceRerankSuccess(
                request.query_fingerprint,
                request.filter_snapshot_ref,
                request.evidence_index_ref,
                request.retrieval_config_ref,
                request.rerank_config_ref,
                request.projection_version,
                request.input_set_hash,
                self.adapter_artifact_ref,
                selections,
            )
        except Exception:
            return EvidenceRerankFailure()


@dataclass(frozen=True, slots=True)
class SyntheticEvidenceSearchAdapter:
    evidence_index: SyntheticEvidenceIndex
    lexical_config: VersionedLexicalSearchConfig
    dense_config: VersionedDenseSearchConfig | None
    adapter_artifact_ref: ImmutableArtifactRef

    def search(
        self,
        request: EvidenceRetrievalKernelRequest,
        stage: EvidenceSearchStage,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure:
        try:
            if (
                request.evidence_index_ref != self.evidence_index.artifact_ref
                or not _valid_evidence_index(self.evidence_index)
                or not _evidence_index_is_bound(self.evidence_index)
                or not _is_synthetic_artifact_ref(self.adapter_artifact_ref)
            ):
                return EvidenceSearchFailure()
            if stage is EvidenceSearchStage.DENSE:
                return self._search_dense(request)
            if (
                stage is not EvidenceSearchStage.LEXICAL
                or request.lexical_config_ref != self.lexical_config.artifact_ref
                or not _valid_lexical_config(self.lexical_config)
                or not _lexical_config_is_bound(self.lexical_config)
            ):
                return EvidenceSearchFailure()
            threshold = Decimal(self.lexical_config.trigram_similarity_threshold)
            if not Decimal(0) <= threshold <= Decimal(1):
                return EvidenceSearchFailure()
            query = _normalized_text(request.normalized_query)
            ranked: list[tuple[bool, Decimal, SyntheticEvidenceRecord]] = []
            for record in self.evidence_index.records:
                content = _normalized_text(record.content_text)
                is_exact = query in content
                score = Decimal(1) if is_exact else _trigram_similarity(query, content)
                if score >= threshold:
                    ranked.append((is_exact, score, record))
            with localcontext(_DECIMAL_CONTEXT):
                ranked.sort(key=lambda item: (-int(item[0]), -item[1], item[2].evidence_key.encode()))
            hits = tuple(
                _search_hit(self.evidence_index.artifact_ref, record, stage, rank, score)
                for rank, (_, score, record) in enumerate(ranked[: request.lexical_limit], start=1)
            )
            return EvidenceSearchSuccess(
                request.query_fingerprint,
                request.filter_snapshot_ref,
                request.evidence_index_ref,
                request.retrieval_config_ref,
                request.lexical_config_ref,
                stage,
                self.adapter_artifact_ref,
                hits,
            )
        except Exception:
            return EvidenceSearchFailure()

    def _search_dense(
        self,
        request: EvidenceRetrievalKernelRequest,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure:
        if (
            self.dense_config is None
            or request.dense_config_ref != self.dense_config.artifact_ref
            or not _valid_dense_config(self.dense_config)
            or not _dense_config_is_bound(self.dense_config)
        ):
            return EvidenceSearchFailure()
        minimum_similarity = Decimal(self.dense_config.minimum_similarity)
        if not Decimal(-1) <= minimum_similarity <= Decimal(1):
            return EvidenceSearchFailure()
        query_vectors = tuple(
            item.values
            for item in self.dense_config.query_vectors
            if item.query_fingerprint == request.query_fingerprint
        )
        if len(query_vectors) != 1:
            return EvidenceSearchFailure()
        query_vector = query_vectors[0]
        ranked: list[tuple[Decimal, SyntheticEvidenceRecord]] = []
        for record in self.evidence_index.records:
            score = _cosine_similarity(query_vector, record.dense_vector)
            if score >= minimum_similarity:
                ranked.append((score, record))
        with localcontext(_DECIMAL_CONTEXT):
            ranked.sort(key=lambda item: (-item[0], item[1].evidence_key.encode()))
        hits = tuple(
            _search_hit(self.evidence_index.artifact_ref, record, EvidenceSearchStage.DENSE, rank, score)
            for rank, (score, record) in enumerate(ranked[: request.dense_limit], start=1)
        )
        return EvidenceSearchSuccess(
            request.query_fingerprint,
            request.filter_snapshot_ref,
            request.evidence_index_ref,
            request.retrieval_config_ref,
            self.dense_config.artifact_ref,
            EvidenceSearchStage.DENSE,
            self.adapter_artifact_ref,
            hits,
        )


def _search_hit(
    evidence_index_ref: ImmutableArtifactRef,
    record: SyntheticEvidenceRecord,
    stage: EvidenceSearchStage,
    rank: int,
    score: Decimal,
) -> KnowledgeEvidenceSearchHit:
    provenance = KnowledgeEvidenceProvenance(
        record.evidence_key,
        record.knowledge_chunk_ref,
        evidence_index_ref,
        record.source_snapshot_ref,
        record.source_version,
        record.locator,
        _source_content_sha256(record.content_text),
        record.canonicalization_spec_version,
    )
    return KnowledgeEvidenceSearchHit(
        provenance,
        stage,
        rank,
        CanonicalScore(_canonical_decimal(score)),
        record.content_text,
    )


def _cosine_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        if not left or len(left) != len(right):
            raise ValueError("dense vector dimensions must match")
        left_values = tuple(Decimal(value) for value in left)
        right_values = tuple(Decimal(value) for value in right)
        if not all(value.is_finite() for value in left_values + right_values):
            raise ValueError("dense vectors must be finite")
        left_norm = sum((value * value for value in left_values), Decimal(0)).sqrt()
        right_norm = sum((value * value for value in right_values), Decimal(0)).sqrt()
        if left_norm == 0 or right_norm == 0:
            raise ValueError("dense vectors must be non-zero")
        return sum(
            (left_value * right_value for left_value, right_value in zip(left_values, right_values, strict=True)),
            Decimal(0),
        ) / (left_norm * right_norm)


def _weighted_score(stage_signals: tuple[StageSignal, ...], lexical_weight: Decimal, dense_weight: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        observed: dict[EvidenceSearchStage, Decimal] = {}
        for signal in stage_signals:
            stage = signal.stage
            score = Decimal(signal.score.value)
            if (
                stage in observed
                or stage not in (EvidenceSearchStage.LEXICAL, EvidenceSearchStage.DENSE)
                or not score.is_finite()
            ):
                raise ValueError("invalid stage signal")
            observed[stage] = score
        return (
            observed.get(EvidenceSearchStage.LEXICAL, Decimal(0)) * lexical_weight
            + observed.get(EvidenceSearchStage.DENSE, Decimal(0)) * dense_weight
        )


def _valid_rerank_candidates(request: EvidenceRerankRequest) -> bool:
    if not isinstance(request.candidates, tuple) or not request.candidates:
        return False
    evidence_keys: set[str] = set()
    stage_ranks: dict[EvidenceSearchStage, set[int]] = {}
    for candidate in request.candidates:
        if type(candidate) is not KnowledgeEvidenceCandidate:
            return False
        provenance = candidate.provenance
        if (
            not _valid_provenance(provenance)
            or provenance.evidence_key in evidence_keys
            or provenance.evidence_index_ref != request.evidence_index_ref
            or type(candidate.content_text) is not SensitiveText
            or _source_content_sha256(candidate.content_text) != provenance.content_sha256
            or not isinstance(candidate.stage_signals, tuple)
            or not candidate.stage_signals
        ):
            return False
        stages: set[EvidenceSearchStage] = set()
        for signal in candidate.stage_signals:
            if (
                not isinstance(signal, StageSignal)
                or signal.stage in stages
                or signal.stage not in (EvidenceSearchStage.LEXICAL, EvidenceSearchStage.DENSE)
                or isinstance(signal.rank, bool)
                or signal.rank <= 0
                or signal.rank in stage_ranks.setdefault(signal.stage, set())
                or not isinstance(signal.score, CanonicalScore)
                or not isinstance(signal.score.value, str)
                or _CANONICAL_SCORE_RE.fullmatch(signal.score.value) is None
            ):
                return False
            score = Decimal(signal.score.value)
            if not score.is_finite():
                return False
            stages.add(signal.stage)
            stage_ranks[signal.stage].add(signal.rank)
        evidence_keys.add(provenance.evidence_key)
    return all(ranks == set(range(1, len(ranks) + 1)) for ranks in stage_ranks.values())


def _valid_provenance(value: KnowledgeEvidenceProvenance) -> bool:
    return (
        isinstance(value, KnowledgeEvidenceProvenance)
        and all(
            _nonempty_nfc(item)
            for item in (
                value.evidence_key,
                value.knowledge_chunk_ref,
                value.source_version,
                value.locator,
                value.canonicalization_spec_version,
            )
        )
        and _valid_artifact_ref(value.evidence_index_ref)
        and _valid_artifact_ref(value.source_snapshot_ref)
        and _has_synthetic_source_marker(value.source_snapshot_ref, value.source_version)
        and isinstance(value.content_sha256, str)
        and _SHA256_RE.fullmatch(value.content_sha256) is not None
    )


def _normalized_text(value: SensitiveText) -> str:
    return " ".join(unicodedata.normalize("NFC", value.reveal()).casefold().split())


def _trigram_similarity(left: str, right: str) -> Decimal:
    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    shared_count = len(left_trigrams & right_trigrams)
    denominator = len(left_trigrams) + len(right_trigrams) - shared_count
    if denominator == 0:
        return Decimal(0)
    with localcontext(_DECIMAL_CONTEXT):
        return Decimal(shared_count) / Decimal(denominator)


def _trigrams(value: str) -> frozenset[str]:
    words: list[str] = []
    current: list[str] = []
    for character in value:
        if character.isalnum():
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return frozenset(
        padded[index : index + 3] for word in words for padded in (f"  {word} ",) for index in range(len(padded) - 2)
    )


def _source_content_sha256(value: SensitiveText) -> str:
    return hashlib.sha256(value.reveal().encode()).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    with localcontext(_DECIMAL_CONTEXT):
        quantized = value.quantize(_SCORE_PLACES)
    if quantized == 0:
        return "0"
    rendered = format(quantized, "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _artifact_ref(artifact_code: str, version: str, payload: object) -> ImmutableArtifactRef:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ImmutableArtifactRef(artifact_code, version, digest)


def _decimal_context_payload() -> dict[str, object]:
    return {"precision": _DECIMAL_CONTEXT.prec, "rounding": "ROUND_HALF_EVEN"}


def _query_vector_sort_key(value: SyntheticDenseQueryVector) -> tuple[bytes, bytes, bytes]:
    fingerprint = value.query_fingerprint
    return (
        fingerprint.algorithm.encode(),
        fingerprint.key_version.encode(),
        fingerprint.digest.encode(),
    )


def _artifact_dict(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "version": value.version,
        "content_sha256": value.content_sha256,
    }


def _valid_artifact_ref(value: ImmutableArtifactRef) -> bool:
    return (
        bool(value.artifact_code.strip())
        and bool(value.version.strip())
        and len(value.content_sha256) == 64
        and all(character in "0123456789abcdef" for character in value.content_sha256)
    )


def _evidence_index_is_bound(value: SyntheticEvidenceIndex) -> bool:
    expected = SyntheticEvidenceIndex.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        value.records,
    )
    return expected.artifact_ref == value.artifact_ref


def _valid_evidence_index(value: SyntheticEvidenceIndex) -> bool:
    if (
        not isinstance(value, SyntheticEvidenceIndex)
        or not _is_synthetic_artifact_ref(value.artifact_ref)
        or not isinstance(value.records, tuple)
    ):
        return False
    evidence_keys: set[str] = set()
    chunk_refs: set[str] = set()
    for record in value.records:
        if (
            type(record) is not SyntheticEvidenceRecord
            or type(record.content_text) is not SensitiveText
            or not isinstance(record.dense_vector, tuple)
            or not record.dense_vector
            or not all(_is_canonical_decimal(component) for component in record.dense_vector)
        ):
            return False
        content = record.content_text.reveal()
        if (
            not _nonempty_nfc(record.evidence_key)
            or not _nonempty_nfc(record.knowledge_chunk_ref)
            or record.evidence_key in evidence_keys
            or record.knowledge_chunk_ref in chunk_refs
            or not _valid_artifact_ref(record.source_snapshot_ref)
            or not _nonempty_nfc(record.source_version)
            or not _has_synthetic_source_marker(
                record.source_snapshot_ref,
                record.source_version,
            )
            or not _nonempty_nfc(record.locator)
            or not _nonempty_nfc(record.canonicalization_spec_version)
            or not _nonempty_nfc(content)
        ):
            return False
        vector = tuple(Decimal(component) for component in record.dense_vector)
        if not all(component.is_finite() for component in vector):
            return False
        evidence_keys.add(record.evidence_key)
        chunk_refs.add(record.knowledge_chunk_ref)
    return True


def _nonempty_nfc(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and unicodedata.normalize("NFC", value) == value


def _lexical_config_is_bound(value: VersionedLexicalSearchConfig) -> bool:
    expected = VersionedLexicalSearchConfig.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        trigram_similarity_threshold=value.trigram_similarity_threshold,
    )
    return expected.artifact_ref == value.artifact_ref


def _valid_lexical_config(value: VersionedLexicalSearchConfig) -> bool:
    return (
        isinstance(value, VersionedLexicalSearchConfig)
        and _is_synthetic_artifact_ref(value.artifact_ref)
        and _is_canonical_decimal(value.trigram_similarity_threshold)
    )


def _dense_config_is_bound(value: VersionedDenseSearchConfig) -> bool:
    expected = VersionedDenseSearchConfig.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        query_vectors=value.query_vectors,
        minimum_similarity=value.minimum_similarity,
    )
    return expected.artifact_ref == value.artifact_ref


def _valid_dense_config(value: VersionedDenseSearchConfig) -> bool:
    if (
        not isinstance(value, VersionedDenseSearchConfig)
        or not _is_synthetic_artifact_ref(value.artifact_ref)
        or not _is_canonical_decimal(value.minimum_similarity)
        or not isinstance(value.query_vectors, tuple)
        or not value.query_vectors
    ):
        return False
    fingerprints: set[QueryFingerprint] = set()
    dimensions: set[int] = set()
    for item in value.query_vectors:
        if (
            not isinstance(item, SyntheticDenseQueryVector)
            or not _valid_fingerprint(item.query_fingerprint)
            or item.query_fingerprint in fingerprints
            or not isinstance(item.values, tuple)
            or not item.values
            or not all(_is_canonical_decimal(component) for component in item.values)
        ):
            return False
        vector = tuple(Decimal(component) for component in item.values)
        if not all(component.is_finite() for component in vector) or all(component == 0 for component in vector):
            return False
        fingerprints.add(item.query_fingerprint)
        dimensions.add(len(vector))
    return len(dimensions) == 1


def _is_canonical_decimal(value: object) -> bool:
    return isinstance(value, str) and _CANONICAL_SCORE_RE.fullmatch(value) is not None


def _valid_fingerprint(value: QueryFingerprint) -> bool:
    return (
        isinstance(value, QueryFingerprint)
        and _nonempty_nfc(value.algorithm)
        and _nonempty_nfc(value.key_version)
        and isinstance(value.digest, str)
        and _SHA256_RE.fullmatch(value.digest) is not None
    )


def _rerank_config_is_bound(value: VersionedRerankConfig) -> bool:
    expected = VersionedRerankConfig.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        lexical_weight=value.lexical_weight,
        dense_weight=value.dense_weight,
        top_k=value.top_k,
    )
    return expected.artifact_ref == value.artifact_ref


def _valid_rerank_config(value: VersionedRerankConfig) -> bool:
    return (
        isinstance(value, VersionedRerankConfig)
        and _is_synthetic_artifact_ref(value.artifact_ref)
        and _is_canonical_decimal(value.lexical_weight)
        and _is_canonical_decimal(value.dense_weight)
        and isinstance(value.top_k, int)
        and not isinstance(value.top_k, bool)
    )


def _is_synthetic_artifact_ref(value: ImmutableArtifactRef) -> bool:
    return _valid_artifact_ref(value) and (
        value.artifact_code.startswith("synthetic-")
        or value.version.startswith("synthetic-")
        or "@synthetic-" in value.version
    )


def _has_synthetic_source_marker(
    source_snapshot_ref: ImmutableArtifactRef,
    source_version: str,
) -> bool:
    normalized_source_version = source_version.casefold()
    return (
        _is_synthetic_artifact_ref(source_snapshot_ref)
        or normalized_source_version.startswith("synthetic@")
        or "-synthetic@" in normalized_source_version
    )
