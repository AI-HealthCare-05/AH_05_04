# Issue #178 RAG Evidence Retrieval Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` while implementing each task. Execute this plan inline and sequentially because all tasks modify the same production and test files.

**Goal:** 비권위적 `KNOWLEDGE_CHUNK` Retrieval Kernel을 구현해 query binding, lexical·dense search, canonical candidate 구성, rerank와 sanitized diagnostic trace를 결정적으로 검증한다.

**Architecture:** 하나의 순수 모듈이 provisional 내부 타입과 orchestration을 소유하고, query 검증·검색·rerank 계산은 Protocol 뒤로 둔다. Kernel은 요청과 실제 적용 Receipt를 exact-match하고 민감한 query·Evidence content를 `SensitiveText`로 격리하지만 Source 승인, Evidence sufficiency, Safety 상태, persistence 또는 Composer 사용 가능성은 판정하지 않는다.

**Tech Stack:** Python 3.13, frozen dataclass, `StrEnum`, `Protocol`, SHA-256, canonical JSON, pytest, Ruff, mypy

**Spec:** `docs/designs/ceohwj/issue-178-rag-evidence-retrieval-design.md`

## Global Constraints

- Candidate Index와 Evidence Index 타입·포트·score를 공유하지 않는다.
- 허용 Evidence kind는 `KNOWLEDGE_CHUNK` 하나이며 `RULE_EVIDENCE`는 구현하지 않는다.
- PostgreSQL, SQLAlchemy, Backend model, sentence-transformers, Source Guard, Safety Result와 Composer를 import하지 않는다.
- 새로운 dependency를 추가하지 않는다.
- Production adapter, DB schema, wire DTO, Runtime Bundle field와 persistence contract를 만들지 않는다.
- `SensitiveText` 원문은 `repr`, `str`, typed failure, trace 또는 기본 JSON serialization에서 노출하지 않는다.
- Kernel output은 비권위적이며 Source approval, sufficiency, conflict, freshness, Safety 또는 Composer eligibility를 주장하지 않는다.
- `.claude/`와 `skills-lock.json` 등 사용자 소유 미추적 파일을 수정하거나 stage하지 않는다.
- 각 Task는 RED 확인 후 최소 구현으로 GREEN을 만들고 해당 Task의 전체 회귀 테스트를 다시 실행한다.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `ai_worker/tasks/rag/evidence_retrieval.py` | provisional 내부 타입, 민감 문자열 wrapper, 입력 검증, search/rerank Receipt와 결과 검증, orchestration, sanitized trace |
| `ai_worker/tests/rag/test_evidence_retrieval.py` | 비식별 합성 fixture, deterministic fake ports, RED/GREEN 및 privacy·determinism 회귀 |

`ai_worker/tasks/rag/__init__.py`에는 새 이름을 export하지 않는다. Knowledge Evidence Index와 Privacy 계약 전에는 이 모듈을 stable package surface로 승격하지 않는다.

---

### Task 1: 민감 입력·불변 참조·Query Binding 경계

**Files:**

- Create: `ai_worker/tasks/rag/evidence_retrieval.py`
- Create: `ai_worker/tests/rag/test_evidence_retrieval.py`

**Interfaces:**

- Produces: `SensitiveText`, `QueryFingerprint`, `ImmutableArtifactRef`, `EvidenceRetrievalKernelRequest`
- Produces: `KernelExecutionStatus`, `KernelDiagnosticCode`
- Produces: `QueryBindingVerificationSuccess`, `QueryBindingVerificationFailure`, `QueryBindingVerifierPort`
- Produces: `EvidenceRetrievalKernelOutcome`
- Produces: `retrieve_knowledge_evidence(...)`의 request/query-validation 초기 경계

- [ ] **Step 1: 테스트 파일에 민감 문자열과 기본 request fixture를 작성한다**

```python
import dataclasses
import json
from dataclasses import replace

import pytest

from ai_worker.tasks.rag.evidence_retrieval import (
    EvidenceRetrievalKernelRequest,
    ImmutableArtifactRef,
    KernelDiagnosticCode,
    KernelExecutionStatus,
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryFingerprint,
    SensitiveText,
    retrieve_knowledge_evidence,
)


def artifact(code: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=f"{code}@1",
        content_sha256="a" * 64,
    )


def fingerprint() -> QueryFingerprint:
    return QueryFingerprint(
        algorithm="HMAC-SHA-256",
        key_version="query-hmac@1",
        digest="b" * 64,
    )


def lexical_request() -> EvidenceRetrievalKernelRequest:
    return EvidenceRetrievalKernelRequest(
        normalized_query=SensitiveText("합성 복약 정보"),
        query_fingerprint=fingerprint(),
        filter_snapshot_ref=artifact("filter-snapshot"),
        evidence_index_ref=artifact("knowledge-index"),
        retrieval_config_ref=artifact("retrieval-config"),
        lexical_config_ref=artifact("lexical-config"),
        dense_config_ref=None,
        rerank_config_ref=artifact("rerank-config"),
        rerank_input_projection_version="knowledge-rerank-input-v1",
        lexical_limit=5,
        dense_limit=0,
        selection_limit=3,
    )
```

- [ ] **Step 2: `SensitiveText` 표현과 직렬화 차단 테스트를 작성한다**

```python
def test_sensitive_text_redacts_representation_and_is_not_json_serializable() -> None:
    secret = "합성 질문 원문 sentinel"
    value = SensitiveText(secret)

    assert str(value) == "<redacted>"
    assert repr(value) == "<redacted>"
    assert value.reveal() == secret

    request = lexical_request()
    assert secret not in repr(replace(request, normalized_query=value))
    with pytest.raises(TypeError):
        json.dumps(dataclasses.asdict(replace(request, normalized_query=value)))
```

- [ ] **Step 3: Query Binding 세 결과와 search 미호출 테스트를 작성한다**

```python
class QueryVerifier:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def verify(self, query: SensitiveText, expected: QueryFingerprint) -> object:
        self.calls += 1
        return self.result


class NeverSearch:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, request: EvidenceRetrievalKernelRequest, stage: object) -> object:
        self.calls += 1
        raise AssertionError("search must not run")


class NeverRerank:
    def rerank(self, request: object) -> object:
        raise AssertionError("rerank must not run")


def query_success() -> QueryBindingVerificationSuccess:
    return QueryBindingVerificationSuccess(
        query_fingerprint=fingerprint(),
        adapter_artifact_ref=artifact("query-verifier"),
    )


@pytest.mark.parametrize(
    ("verification", "status", "code"),
    [
        (
            QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING),
            KernelExecutionStatus.VALIDATION_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_INVALID,
        ),
        (
            QueryBindingVerificationFailure(QueryBindingFailureReason.DEPENDENCY_ERROR),
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR,
        ),
        (
            QueryBindingVerificationSuccess(
                query_fingerprint=replace(fingerprint(), digest="c" * 64),
                adapter_artifact_ref=artifact("query-verifier"),
            ),
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH,
        ),
    ],
)
def test_query_binding_failures_stop_before_search(
    verification: object,
    status: KernelExecutionStatus,
    code: KernelDiagnosticCode,
) -> None:
    verifier = QueryVerifier(verification)
    search = NeverSearch()

    result = retrieve_knowledge_evidence(lexical_request(), verifier, search, NeverRerank())

    assert result.execution_status is status
    assert result.diagnostic_code is code
    assert verifier.calls == 1
    assert search.calls == 0
```

- [ ] **Step 4: 테스트를 실행해 모듈 부재로 RED인지 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'ai_worker.tasks.rag.evidence_retrieval'`.

- [ ] **Step 5: production 모듈에 Task 1 타입과 검증을 최소 구현한다**

```python
"""Non-authoritative, synthetic-first Knowledge Evidence retrieval kernel."""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RERANK_INPUT_PROJECTION_VERSION = "knowledge-rerank-input-v1"


class SensitiveText:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    algorithm: str
    key_version: str
    digest: str


@dataclass(frozen=True, slots=True)
class ImmutableArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalKernelRequest:
    normalized_query: SensitiveText
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    lexical_config_ref: ImmutableArtifactRef
    dense_config_ref: ImmutableArtifactRef | None
    rerank_config_ref: ImmutableArtifactRef
    rerank_input_projection_version: str
    lexical_limit: int
    dense_limit: int
    selection_limit: int


class KernelExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class KernelDiagnosticCode(StrEnum):
    CANDIDATES_RERANKED = "CANDIDATES_RERANKED"
    NO_HITS = "NO_HITS"
    QUERY_BINDING_INVALID = "QUERY_BINDING_INVALID"
    REQUEST_INVALID = "REQUEST_INVALID"
    QUERY_BINDING_RECEIPT_MISMATCH = "QUERY_BINDING_RECEIPT_MISMATCH"
    QUERY_BINDING_DEPENDENCY_ERROR = "QUERY_BINDING_DEPENDENCY_ERROR"
    SEARCH_DEPENDENCY_ERROR = "SEARCH_DEPENDENCY_ERROR"
    SEARCH_RECEIPT_MISMATCH = "SEARCH_RECEIPT_MISMATCH"
    SEARCH_RESULT_INVALID = "SEARCH_RESULT_INVALID"
    RERANK_DEPENDENCY_ERROR = "RERANK_DEPENDENCY_ERROR"
    RERANK_RECEIPT_MISMATCH = "RERANK_RECEIPT_MISMATCH"
    RERANK_RESULT_INVALID = "RERANK_RESULT_INVALID"


class QueryBindingFailureReason(StrEnum):
    INVALID_BINDING = "INVALID_BINDING"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationSuccess:
    query_fingerprint: QueryFingerprint
    adapter_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationFailure:
    reason: QueryBindingFailureReason


class QueryBindingVerifierPort(Protocol):
    def verify(
        self,
        query: SensitiveText,
        expected: QueryFingerprint,
    ) -> QueryBindingVerificationSuccess | QueryBindingVerificationFailure: ...


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalKernelOutcome:
    execution_status: KernelExecutionStatus
    diagnostic_code: KernelDiagnosticCode
    untrusted_selections: tuple[object, ...] = ()
    failure_details: tuple[str, ...] = ()
```

`_request_is_valid(...)`은 다음을 실제 runtime type으로 검사한다.

```python
def _is_nonblank_nfc(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and unicodedata.is_normalized("NFC", value)


def _artifact_ref_is_valid(value: object) -> bool:
    return (
        isinstance(value, ImmutableArtifactRef)
        and _is_nonblank_nfc(value.artifact_code)
        and _is_nonblank_nfc(value.version)
        and isinstance(value.content_sha256, str)
        and _SHA256_RE.fullmatch(value.content_sha256) is not None
    )
```

Request 검증에는 `SensitiveText` instance, nonblank NFC revealed query, fingerprint 세 필드, 모든 artifact ref, bool을 제외한 정수 limit, dense ref/limit 조합, projection version exact-match를 포함한다.

`retrieve_knowledge_evidence(...)`는 아직 search 성공을 구현하지 않고 다음 순서까지만 제공한다.

1. request invalid → `VALIDATION_ERROR/REQUEST_INVALID`
2. verifier exception → message를 버리고 `DEPENDENCY_ERROR/QUERY_BINDING_DEPENDENCY_ERROR`
3. typed invalid/dependency failure 매핑
4. success 타입·fingerprint·adapter reference 불일치 → `DEPENDENCY_ERROR/QUERY_BINDING_RECEIPT_MISMATCH`
5. valid query 이후에는 Task 2가 사용할 private orchestration helper로 진입

- [ ] **Step 6: Task 1 테스트가 GREEN인지 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: Task 1 tests pass. Search success 경로를 아직 테스트하지 않는다.

- [ ] **Step 7: lint와 type check를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag/evidence_retrieval.py
```

Expected: both exit code 0.

- [ ] **Step 8: Task 1만 커밋한다**

```bash
git add ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
git commit -m "✨ feat: Retrieval Kernel 입력 경계 추가"
```

---

### Task 2: Search Receipt·Hit 검증과 Canonical Candidate 정규화

**Files:**

- Modify: `ai_worker/tasks/rag/evidence_retrieval.py`
- Modify: `ai_worker/tests/rag/test_evidence_retrieval.py`

**Interfaces:**

- Consumes: Task 1 request, fingerprint, artifact reference와 query verifier
- Produces: `EvidenceSearchStage`, `CanonicalScore`, `KnowledgeEvidenceProvenance`
- Produces: `KnowledgeEvidenceSearchHit`, `EvidenceSearchSuccess`, `EvidenceSearchFailure`, `EvidenceSearchPort`
- Produces: `StageSignal`, `KnowledgeEvidenceCandidate`

- [ ] **Step 1: 합성 Knowledge Evidence helper와 lexical/dense fake port를 테스트에 추가한다**

```python
import hashlib


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance(
    *,
    evidence_key: str = "knowledge:chunk-1",
    chunk_ref: str = "chunk-1",
    text: str = "합성 복약 근거",
) -> KnowledgeEvidenceProvenance:
    return KnowledgeEvidenceProvenance(
        evidence_key=evidence_key,
        knowledge_chunk_ref=chunk_ref,
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=artifact("source-snapshot"),
        source_version="mfds-synthetic@1",
        locator="$.records.synthetic-1",
        content_sha256=content_hash(text),
        canonicalization_spec_version="knowledge-text@1",
    )


def hit(
    stage: EvidenceSearchStage,
    *,
    rank: int = 1,
    score: str = "0.9",
    evidence: KnowledgeEvidenceProvenance | None = None,
    text: str = "합성 복약 근거",
) -> KnowledgeEvidenceSearchHit:
    return KnowledgeEvidenceSearchHit(
        provenance=evidence or provenance(text=text),
        stage=stage,
        rank=rank,
        stage_score=CanonicalScore(score),
        content_text=SensitiveText(text),
    )
```

- [ ] **Step 2: 정상 lexical 및 mixed-stage candidate 정규화 테스트를 작성한다**

```python
def test_search_results_normalize_same_evidence_into_one_candidate() -> None:
    lexical = hit(EvidenceSearchStage.LEXICAL, score="0.9")
    dense = hit(EvidenceSearchStage.DENSE, score="0.8")
    request = replace(
        lexical_request(),
        dense_config_ref=artifact("dense-config"),
        dense_limit=5,
    )
    search = SearchPort(
        {
            EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, (lexical,)),
            EvidenceSearchStage.DENSE: search_success(request, EvidenceSearchStage.DENSE, (dense,)),
        }
    )
    rerank = CapturingRerankPort()

    retrieve_knowledge_evidence(request, QueryVerifier(query_success()), search, rerank)

    assert rerank.request is not None
    assert len(rerank.request.candidates) == 1
    assert rerank.request.candidates[0].stage_signals == (
        StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),
        StageSignal(EvidenceSearchStage.DENSE, 1, CanonicalScore("0.8")),
    )
```

이 RED 단계의 `CapturingRerankPort`는 전달된 request를 저장한 뒤 `EvidenceRerankFailure`를 반환한다. Task 2는 rerank 결과 검증을 아직 구현하지 않는다.

- [ ] **Step 3: Search fail-closed parameterized tests를 작성한다**

다음 mutation을 각각 독립 case로 만든다.

```python
@pytest.mark.parametrize(
    "mutation",
    [
        "receipt_query_fingerprint",
        "receipt_filter_snapshot",
        "receipt_evidence_index",
        "receipt_retrieval_config",
        "receipt_stage_config",
        "receipt_stage",
        "missing_adapter_artifact",
        "rank_starts_at_zero",
        "rank_gap",
        "rank_duplicate",
        "stage_mismatch",
        "stage_limit_exceeded",
        "score_negative_zero",
        "score_exponent",
        "score_trailing_zero",
        "content_hash_mismatch",
        "candidate_index_hit_type",
        "duplicate_key_in_stage",
        "same_key_different_content_across_stages",
        "same_key_different_provenance_across_stages",
        "same_chunk_different_evidence_key",
    ],
)
def test_invalid_search_output_fails_closed(mutation: str) -> None:
    result = execute_mutated_search_case(mutation)

    assert result.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert result.diagnostic_code in {
        KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH,
        KernelDiagnosticCode.SEARCH_RESULT_INVALID,
    }
    assert result.untrusted_selections == ()
```

`candidate_index_hit_type`은 `ai_worker.tasks.rag.candidate_index.CandidateRawHit`을 tuple에 넣어 runtime `isinstance` 검증이 거부하는지 확인한다. Production 모듈은 Candidate 모듈을 import하지 않는다.

- [ ] **Step 4: 테스트를 실행해 새 타입 또는 동작 부재로 RED인지 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: missing search types or missing normalization assertions fail.

- [ ] **Step 5: Search 타입과 canonical score를 구현한다**

```python
_CANONICAL_SCORE_RE = re.compile(r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$")


class EvidenceSearchStage(StrEnum):
    LEXICAL = "LEXICAL"
    DENSE = "DENSE"


@dataclass(frozen=True, slots=True)
class CanonicalScore:
    value: str


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceProvenance:
    evidence_key: str
    knowledge_chunk_ref: str
    evidence_index_ref: ImmutableArtifactRef
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    canonicalization_spec_version: str


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceSearchHit:
    provenance: KnowledgeEvidenceProvenance
    stage: EvidenceSearchStage
    rank: int
    stage_score: CanonicalScore
    content_text: SensitiveText


@dataclass(frozen=True, slots=True)
class StageSignal:
    stage: EvidenceSearchStage
    rank: int
    score: CanonicalScore


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceCandidate:
    provenance: KnowledgeEvidenceProvenance
    content_text: SensitiveText
    stage_signals: tuple[StageSignal, ...]
```

`EvidenceSearchSuccess`는 설계의 Search Receipt 필드와 `hits`를 가진다. `EvidenceSearchFailure`는 safe enum reason만 가지며 detail 또는 exception message를 저장하지 않는다.

```python
class EvidenceSearchPort(Protocol):
    def search(
        self,
        request: EvidenceRetrievalKernelRequest,
        stage: EvidenceSearchStage,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure: ...
```

- [ ] **Step 6: Search 검증과 candidate normalization을 구현한다**

구현 순서는 다음으로 고정한다.

1. lexical search 호출 및 typed failure/exception 매핑
2. Search Receipt의 fingerprint, filter, index, retrieval config, lexical config, stage exact-match
3. adapter ref, hit runtime type, rank, stage, limit, canonical score, content hash, provenance 검증
4. dense 활성 시 같은 검증 반복; 비활성 시 dense 호출 금지
5. 동일 stage duplicate key 거부
6. cross-stage 동일 key의 provenance/content exact-match
7. `knowledge_chunk_ref → evidence_key`와 `evidence_key → provenance` 일대일 검증
8. `LEXICAL`, `DENSE` 순서의 `StageSignal` tuple로 candidate 구성
9. 두 stage가 모두 empty면 rerank 미호출 `SUCCEEDED/NO_HITS`

Canonical score는 wrapper type뿐 아니라 `.value`가 정규식에 일치하는지 runtime 검증한다. Content hash는 `hashlib.sha256(content_text.reveal().encode("utf-8")).hexdigest()`로 계산하며 normalize하지 않는다.

- [ ] **Step 7: Task 2 테스트와 전체 기존 RAG 테스트를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag -q
```

Expected: all pass.

- [ ] **Step 8: lint와 mypy를 실행한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
```

Expected: both exit code 0.

- [ ] **Step 9: Task 2를 커밋한다**

```bash
git add ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
git commit -m "✨ feat: Knowledge Evidence 검색 결과 검증 추가"
```

---

### Task 3: Versioned Rerank Projection·Selection 검증

**Files:**

- Modify: `ai_worker/tasks/rag/evidence_retrieval.py`
- Modify: `ai_worker/tests/rag/test_evidence_retrieval.py`

**Interfaces:**

- Consumes: Task 2 canonical candidates
- Produces: `EvidenceRerankRequest`, `EvidenceRerankSelection`, `EvidenceRerankSuccess`, `EvidenceRerankFailure`, `EvidenceRerankPort`
- Produces: `UntrustedKnowledgeEvidenceSelection`
- Produces: `canonical_rerank_input_hash(...)` private helper

- [ ] **Step 1: Golden projection hash test를 먼저 작성한다**

고정 fixture는 Unicode locator, nullable dense config가 없는 lexical-only case, mixed-stage와 음수 score case를 포함한다.

```python
def test_rerank_input_projection_has_stable_golden_hash() -> None:
    candidates = (
        KnowledgeEvidenceCandidate(
            provenance=provenance(),
            content_text=SensitiveText("합성 복약 근거"),
            stage_signals=(
                StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),
                StageSignal(EvidenceSearchStage.DENSE, 2, CanonicalScore("-0.25")),
            ),
        ),
    )

    assert canonical_rerank_input_hash(
        "knowledge-rerank-input-v1",
        candidates,
    ) == "e01b174ebf70c08b24db48efafd908219d77fa242502fe54443fe753fcce894c"
```

위 hash는 설계의 compact, key-sorted UTF-8 JSON 규칙으로 의미 payload를 직렬화한 고정값이다.
`content_sha256`은 `합성 복약 근거` UTF-8 bytes의 SHA-256인
`bc5b556f1bff229bbe6eb6b128b9f3a6d030c805c31c62a9ad933ea44eaa6db2`다. 구현이 다른 byte envelope를
만들면 테스트를 수정하지 말고 serializer를 이 계약에 맞춘다.

- [ ] **Step 2: Rerank 성공과 fail-closed 테스트를 작성한다**

```python
def test_valid_rerank_rebinds_selection_to_canonical_candidate() -> None:
    result = run_valid_mixed_stage_retrieval()

    assert result.execution_status is KernelExecutionStatus.SUCCEEDED
    assert result.diagnostic_code is KernelDiagnosticCode.CANDIDATES_RERANKED
    assert len(result.untrusted_selections) == 1
    assert result.untrusted_selections[0].candidate.provenance.evidence_key == "knowledge:chunk-1"
    assert result.untrusted_selections[0].candidate.content_text.reveal() == "합성 복약 근거"


@pytest.mark.parametrize(
    "mutation",
    [
        "receipt_query_fingerprint",
        "receipt_filter_snapshot",
        "receipt_evidence_index",
        "receipt_retrieval_config",
        "receipt_rerank_config",
        "missing_adapter_artifact",
        "projection_version",
        "input_set_hash",
        "unknown_evidence_key",
        "duplicate_evidence_key",
        "rank_starts_at_zero",
        "rank_gap",
        "selection_limit_exceeded",
        "score_negative_zero",
        "score_exponent",
    ],
)
def test_invalid_rerank_output_fails_closed(mutation: str) -> None:
    result = execute_mutated_rerank_case(mutation)

    assert result.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert result.diagnostic_code in {
        KernelDiagnosticCode.RERANK_RECEIPT_MISMATCH,
        KernelDiagnosticCode.RERANK_RESULT_INVALID,
    }
    assert result.untrusted_selections == ()
```

- [ ] **Step 3: 테스트를 실행해 rerank 기능 부재로 RED인지 확인한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: missing rerank types/helper or absent selection behavior fails.

- [ ] **Step 4: Rerank 타입과 canonical projection을 구현한다**

```python
@dataclass(frozen=True, slots=True)
class EvidenceRerankRequest:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    rerank_config_ref: ImmutableArtifactRef
    projection_version: str
    input_set_hash: str
    candidates: tuple[KnowledgeEvidenceCandidate, ...]


@dataclass(frozen=True, slots=True)
class EvidenceRerankSelection:
    evidence_key: str
    rerank_rank: int
    rerank_score: CanonicalScore


@dataclass(frozen=True, slots=True)
class UntrustedKnowledgeEvidenceSelection:
    candidate: KnowledgeEvidenceCandidate
    rerank_rank: int
    rerank_score: CanonicalScore


class EvidenceRerankPort(Protocol):
    def rerank(
        self,
        request: EvidenceRerankRequest,
    ) -> EvidenceRerankSuccess | EvidenceRerankFailure: ...
```

Canonical payload helper는 dataclass generic serialization을 사용하지 않고 모든 필드를 명시적으로 투영한다.

```python
def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def canonical_rerank_input_hash(
    projection_version: str,
    candidates: tuple[KnowledgeEvidenceCandidate, ...],
) -> str:
    payload = {
        "projection_version": projection_version,
        "candidates": [_candidate_projection(item) for item in candidates],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
```

`_candidate_projection(...)`은 다음 필드를 빠짐없이 넣는다.

- evidence key와 chunk ref
- Evidence Index artifact code/version/hash
- Source Snapshot artifact code/version/hash
- Source version, locator, content hash, canonicalization spec version
- stage signal의 stage `.value`, rank, canonical score `.value`

Candidate는 evidence key UTF-8 byte 순, signals는 `LEXICAL`, `DENSE` 순으로 정렬한다. 입력이 이미 canonical order가 아니어도 helper가 canonical projection 순서로 정렬한다.

- [ ] **Step 5: Rerank orchestration과 output 검증을 구현한다**

1. Kernel이 projection hash를 계산하고 `EvidenceRerankRequest`를 만든다.
2. port exception은 message 없이 `DEPENDENCY_ERROR/RERANK_DEPENDENCY_ERROR`다.
3. success Receipt의 fingerprint, filter, index, retrieval config, rerank config, projection version/hash를 exact-match한다.
4. adapter artifact ref를 형식 검증하고 비권위적 trace 입력으로 보존한다.
5. selection runtime type, limit, 1-based 연속 rank, canonical score, key uniqueness와 candidate membership을 검증한다.
6. Kernel이 key로 canonical candidate를 찾아 `UntrustedKnowledgeEvidenceSelection`을 만든다.
7. 성공은 `SUCCEEDED/CANDIDATES_RERANKED`; selection 0건도 reranker가 정상 실행했다면 같은 diagnostic을 사용한다.

- [ ] **Step 6: Task 3 테스트와 전체 RAG 회귀를 실행한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag -q
```

Expected: all pass.

- [ ] **Step 7: lint와 mypy를 실행한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
```

Expected: both exit code 0.

- [ ] **Step 8: Task 3을 커밋한다**

```bash
git add ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
git commit -m "✨ feat: Retrieval rerank Receipt 검증 추가"
```

---

### Task 4: Sanitized Trace·Privacy·Determinism 회귀 완성

**Files:**

- Modify: `ai_worker/tasks/rag/evidence_retrieval.py`
- Modify: `ai_worker/tests/rag/test_evidence_retrieval.py`

**Interfaces:**

- Consumes: Task 1–3 kernel outcomes
- Produces: `EvidenceRetrievalDiagnosticTrace`, trace record 타입, `to_sanitized_trace_dict(...)`
- Completes: 모든 success/failure outcome의 sanitized trace

- [ ] **Step 1: Trace 결정성과 privacy 테스트를 작성한다**

```python
def test_same_input_and_port_output_produce_same_sanitized_trace() -> None:
    first = run_valid_mixed_stage_retrieval()
    second = run_valid_mixed_stage_retrieval()

    assert to_sanitized_trace_dict(first.trace) == to_sanitized_trace_dict(second.trace)


def test_trace_and_representations_do_not_expose_query_content_or_exception_message() -> None:
    query_sentinel = "합성 환자 질문 sentinel"
    content_sentinel = "합성 Source 원문 sentinel"
    exception_sentinel = "provider-secret-error-sentinel"
    outcome = run_with_port_exception(
        query=SensitiveText(query_sentinel),
        content=SensitiveText(content_sentinel),
        error=RuntimeError(exception_sentinel),
    )

    trace_json = json.dumps(to_sanitized_trace_dict(outcome.trace), ensure_ascii=False, sort_keys=True)
    rendered = repr(outcome) + str(outcome)

    for sentinel in (query_sentinel, content_sentinel, exception_sentinel):
        assert sentinel not in trace_json
        assert sentinel not in rendered
    assert outcome.failure_details == ()


def test_whole_success_outcome_is_not_default_json_serializable() -> None:
    outcome = run_valid_mixed_stage_retrieval()

    with pytest.raises(TypeError):
        json.dumps(dataclasses.asdict(outcome))
    json.dumps(to_sanitized_trace_dict(outcome.trace))
```

- [ ] **Step 2: Trace 상태·내용 테스트를 작성한다**

```python
def test_sanitized_trace_records_applied_artifacts_without_transient_text() -> None:
    outcome = run_valid_mixed_stage_retrieval()
    trace = to_sanitized_trace_dict(outcome.trace)

    assert trace["execution_status"] == "SUCCEEDED"
    assert trace["diagnostic_code"] == "CANDIDATES_RERANKED"
    assert trace["query_fingerprint"] == {
        "algorithm": "HMAC-SHA-256",
        "key_version": "query-hmac@1",
        "digest": "b" * 64,
    }
    assert trace["adapter_artifacts"]["query_verifier"]["artifact_code"] == "query-verifier"
    assert trace["adapter_artifacts"]["lexical"]["artifact_code"] == "lexical-adapter"
    assert trace["adapter_artifacts"]["dense"]["artifact_code"] == "dense-adapter"
    assert trace["adapter_artifacts"]["rerank"]["artifact_code"] == "rerank-adapter"
    assert "normalized_query" not in trace
    assert "content_text" not in trace["hits"][0]
```

Request-invalid, query-invalid, search failure, no-hit, rerank failure와 success 각각 trace가 존재하고 상태/diagnostic 조합이 설계 표와 일치하는 parameterized test를 추가한다.

- [ ] **Step 3: 테스트를 실행해 trace 부재 또는 privacy 누락으로 RED인지 확인한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: missing trace types/serializer or missing redaction assertions fail.

- [ ] **Step 4: Trace 타입과 명시적 serializer를 구현한다**

Trace record는 민감 문자열을 포함하지 않는 별도 dataclass로 정의한다.

```python
@dataclass(frozen=True, slots=True)
class DiagnosticHitRecord:
    evidence_key: str
    knowledge_chunk_ref: str
    stage: EvidenceSearchStage
    rank: int
    stage_score: CanonicalScore
    content_sha256: str
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalDiagnosticTrace:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    lexical_config_ref: ImmutableArtifactRef
    dense_config_ref: ImmutableArtifactRef | None
    rerank_config_ref: ImmutableArtifactRef
    query_verifier_artifact_ref: ImmutableArtifactRef | None
    lexical_adapter_artifact_ref: ImmutableArtifactRef | None
    dense_adapter_artifact_ref: ImmutableArtifactRef | None
    rerank_adapter_artifact_ref: ImmutableArtifactRef | None
    hits: tuple[DiagnosticHitRecord, ...]
    selections: tuple[DiagnosticSelectionRecord, ...]
    execution_status: KernelExecutionStatus
    diagnostic_code: KernelDiagnosticCode
```

`to_sanitized_trace_dict(...)`은 `dataclasses.asdict`를 호출하지 않고 다음을 명시적으로 투영한다.

- StrEnum은 `.value`
- CanonicalScore는 `.value`
- Artifact ref는 code/version/hash
- nullable adapter ref는 JSON `null`
- hits는 stage/rank 순
- selections는 rerank rank 순

Outcome은 `trace`, `untrusted_selections`, 빈 `failure_details`만 가진다. 어떤 failure path도 exception 객체 또는 message를 저장하지 않는다.

- [ ] **Step 5: 모든 trace/privacy 테스트를 GREEN으로 만든다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
```

Expected: all pass with no warnings.

- [ ] **Step 6: 전체 요구 검증을 실행한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
git diff --check
```

Expected:

- all pytest suites pass
- Ruff: `All checks passed!`
- mypy: `Success: no issues found`
- `git diff --check`: no output, exit code 0

- [ ] **Step 7: Candidate Index import 격리와 금지 문자열을 정적 확인한다**

```bash
rg -n "candidate_index|sqlalchemy|sentence_transformers|backend\.app|source_approved|evidence_status|release_decision|SelectedEvidenceSet|RetrievalRunRecord" ai_worker/tasks/rag/evidence_retrieval.py
```

Expected: no output. 테스트 파일은 rejection fixture 때문에 `candidate_index` import를 포함할 수 있다.

- [ ] **Step 8: 전체 diff를 검토한다**

```bash
git diff -- ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
git status --short
```

Expected: 구현·테스트 두 파일만 변경되며 `.claude/`, `skills-lock.json`은 미추적 상태로 유지된다.

- [ ] **Step 9: Task 4를 커밋한다**

```bash
git add ai_worker/tasks/rag/evidence_retrieval.py ai_worker/tests/rag/test_evidence_retrieval.py
git commit -m "✅ test: Retrieval Kernel privacy 회귀 보강"
```

---

## Final Verification and Review Gate

- [ ] **Step 1: 브랜치 전체의 fresh verification을 실행한다**

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
git diff --check origin/develop...HEAD
```

- [ ] **Step 2: 설계 완료 주장 경계를 점검한다**

최종 보고에는 다음만 완료로 주장한다.

- 합성 Knowledge Evidence에 대한 query/search/rerank orchestration
- requested/applied immutable reference exact-match
- canonical candidate와 rerank input hash 결정성
- rank·score·content·provenance fail-closed 검증
- sanitized non-authoritative diagnostic trace

다음은 완료로 주장하지 않는다.

- Source approval 또는 Runtime Bundle eligibility
- Evidence sufficiency, conflict, freshness 또는 Safety 상태
- PostgreSQL `pg_trgm`·dense 품질
- Retrieval Run persistence
- Rule Evidence, Composer, Citation 또는 Recall@5

- [ ] **Step 3: 독립 code/spec/security와 architecture review를 요청한다**

Review scope:

```text
origin/develop...HEAD
docs/designs/ceohwj/issue-178-rag-evidence-retrieval-design.md
docs/designs/ceohwj/issue-178-rag-evidence-retrieval-implementation-plan.md
ai_worker/tasks/rag/evidence_retrieval.py
ai_worker/tests/rag/test_evidence_retrieval.py
```

Approval requires code/spec/security `APPROVE` and architecture `CLEAR`. Review finding이 있으면 해당 Task의 가장 작은 RED test부터 추가하고 fix한 뒤 전체 verification과 review를 반복한다.

---

## Planned Commit Sequence

1. `✨ feat: Retrieval Kernel 입력 경계 추가`
2. `✨ feat: Knowledge Evidence 검색 결과 검증 추가`
3. `✨ feat: Retrieval rerank Receipt 검증 추가`
4. `✅ test: Retrieval Kernel privacy 회귀 보강`

설계·계획 문서 커밋은 이미 별도로 유지하며 구현 커밋에 `.claude/` 또는 `skills-lock.json`을 포함하지 않는다.
