# Issue #167 RAG 의약품 후보 인덱스 로직 구현 계획

> **에이전트 작업 필수 지침:** 이 계획은 `superpowers:subagent-driven-development`(권장) 또는
> `superpowers:executing-plans`를 사용해 작업별로 구현한다. 모든 단계는 체크박스로 추적한다.

**목표:** 승인된 합성 Catalog export에서 결정적인 의약품 Candidate Index 구성원과 manifest를
생성하고, RAG-07B가 구현할 검색 단계와 내부 raw hit 계약을 제공한다.

**아키텍처:** `ai_worker/tasks/rag/candidate_index.py`가 불변 타입, fail-closed validation,
canonical SHA-256 build와 검색 orchestration을 소유한다. PostgreSQL, pg_trgm, pgvector, migration,
repository와 Candidate Resolver 판정은 구현하지 않고 protocol 뒤에 둔다.

**기술 스택:** Python 3.13, 표준 라이브러리 `dataclasses`·`enum`·`hashlib`·`json`·`math`·
`unicodedata`, `typing.Protocol`, pytest, Ruff, Mypy

**설계 문서:** `docs/designs/ceohwj/issue-167-rag-candidate-index-design.md`

## 전역 제약

- 구현 파일은 `ai_worker/tasks/rag/candidate_index.py` 하나로 시작한다.
- 테스트 파일은 `ai_worker/tests/rag/test_candidate_index.py` 하나로 시작한다.
- DB, migration, repository, API, 환자 DTO와 Evaluation schema를 수정하지 않는다.
- 실제 MFDS·RAG-06 fixture가 없으므로 비식별 합성 record만 사용한다.
- 실패 결과에는 partial member나 manifest를 포함하지 않는다.
- Candidate Index와 의료 Evidence Index를 혼합하지 않는다.
- `PUBLIC_TRACK_F=false`를 변경하지 않는다.
- 통합 테스트는 `BLOCKED_BY_RAG_04_OR_06`으로 보고하며 통과로 간주하지 않는다.
- 각 기능은 실패하는 테스트를 먼저 확인한 뒤 최소 구현을 추가한다.

---

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| `ai_worker/tasks/rag/candidate_index.py` | RAG-07A 공개 타입, Catalog 검증, 결정적 build, embedding 검증, 검색 port와 orchestration |
| `ai_worker/tests/rag/test_candidate_index.py` | 비식별 합성 Catalog, fake embedding/search port, 결정성·실패·검색 순서 회귀 테스트 |
| `docs/designs/ceohwj/issue-167-rag-candidate-index-design.md` | 승인된 설계 정본. 구현 중 의미를 바꾸지 않는다. |
| `docs/designs/ceohwj/issue-167-rag-candidate-index-implementation-plan.md` | 구현·검증 순서와 차단 상태 기록 |

## Task 1: 공개 타입과 Catalog·설정 fail-closed 경계

**파일:**

- 생성: `ai_worker/tasks/rag/candidate_index.py`
- 생성: `ai_worker/tests/rag/test_candidate_index.py`

**인터페이스:**

- 입력: `CandidateCatalogExport`, `CandidateIndexBuildConfig`
- 출력: `CandidateIndexBuildFailure` 또는 후속 Task가 완성할 `CandidateIndexBuildSuccess`
- 생성 함수: `build_candidate_index(catalog, config, embedding_port=None)`

- [x] **Step 1: 실패 enum과 최소 Catalog fixture를 요구하는 테스트 작성**

```python
from dataclasses import replace

from ai_worker.tasks.rag.candidate_index import (
    CandidateIndexBuildFailure,
    CandidateIndexBuildFailureReason,
    build_candidate_index,
)


def test_unapproved_catalog_fails_without_partial_output() -> None:
    result = build_candidate_index(
        replace(valid_catalog(), verification_status="NOT_APPROVED"),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_NOT_APPROVED,
        details=("verification_status",),
    )
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")
```

- [x] **Step 2: 테스트가 올바른 이유로 실패하는지 확인**

실행:

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py::test_unapproved_catalog_fails_without_partial_output -q
```

기대 결과: `ai_worker.tasks.rag.candidate_index`가 없어 collection error가 발생한다. 테스트 파일 import와
이름 오타를 제거한 뒤에도 production module 부재 때문에 실패하는 것을 확인한다.

- [x] **Step 3: enum과 불변 입력·실패 타입의 최소 구현 추가**

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CandidateIndexBuildFailureReason(StrEnum):
    CATALOG_NOT_APPROVED = "CATALOG_NOT_APPROVED"
    CATALOG_STALE = "CATALOG_STALE"
    CATALOG_PARTIAL = "CATALOG_PARTIAL"
    CATALOG_MANIFEST_INVALID = "CATALOG_MANIFEST_INVALID"
    CATALOG_COUNT_MISMATCH = "CATALOG_COUNT_MISMATCH"
    DUPLICATE_PRODUCT_IDENTITY = "DUPLICATE_PRODUCT_IDENTITY"
    REFERENTIAL_INTEGRITY_INVALID = "REFERENTIAL_INTEGRITY_INVALID"
    ALIAS_CONFLICT = "ALIAS_CONFLICT"
    MEMBER_CONFLICT = "MEMBER_CONFLICT"
    BUILD_CONFIG_INVALID = "BUILD_CONFIG_INVALID"
    EMBEDDING_OUTPUT_INVALID = "EMBEDDING_OUTPUT_INVALID"


@dataclass(frozen=True, slots=True)
class CandidateIndexBuildFailure:
    reason: CandidateIndexBuildFailureReason
    details: tuple[str, ...]
```

같은 파일에 `ProductIdentity`, `CatalogProduct`, `CatalogIngredient`, `CatalogComponent`,
`CatalogAlias`, `CatalogSearchEntry`, `CandidateCatalogCounts`, `CandidateCatalogExport`,
`CandidateIndexBuildMode`, `CandidateIndexBuildConfig`를 모두 `frozen=True, slots=True` dataclass로
정의한다. `build_candidate_index(...)`는 승인, freshness, completeness, SHA-256 형식과 설정
nullability를 먼저 검증하고 정확한 failure reason을 반환한다.

- [x] **Step 4: Catalog envelope와 config negative matrix 추가**

```python
@pytest.mark.parametrize(
    ("catalog", "reason"),
    [
        (lambda value: replace(value, freshness_status="STALE"), CandidateIndexBuildFailureReason.CATALOG_STALE),
        (lambda value: replace(value, is_complete=False), CandidateIndexBuildFailureReason.CATALOG_PARTIAL),
        (
            lambda value: replace(value, catalog_manifest_hash="not-sha256"),
            CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID,
        ),
    ],
)
def test_catalog_envelope_fails_closed(catalog, reason) -> None:
    result = build_candidate_index(catalog(valid_catalog()), lexical_config())
    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is reason
```

`LEXICAL_ONLY`에 embedding 필드가 있거나 `HYBRID`에 provider/model/version/dimension/COSINE 설정이
빠진 경우 `BUILD_CONFIG_INVALID`인지 별도 parameterized test로 고정한다.

- [x] **Step 5: Task 1 테스트와 정적 검사를 통과시킨다**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py -q
uv run ruff check ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
uv run mypy ai_worker/tasks/rag/candidate_index.py
```

- [x] **Step 6: Task 1 커밋**

```bash
git add ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
git commit -m "✨ feat: Candidate Index 계약과 검증 경계 추가"
```

## Task 2: 결정적 lexical 구성원과 manifest build

**파일:**

- 수정: `ai_worker/tasks/rag/candidate_index.py`
- 수정: `ai_worker/tests/rag/test_candidate_index.py`

**인터페이스:**

- 소비: Task 1의 Catalog·config·failure 타입
- 생성: `CandidateIndexMember`, `CandidateIndexManifest`, `CandidateIndexBuildSuccess`
- 완성: `build_candidate_index(...)`의 `LEXICAL_ONLY` 경로

- [x] **Step 1: 입력 순서와 중복에 무관한 결정성 테스트 작성**

```python
def test_lexical_build_is_deterministic_across_input_order() -> None:
    forward = build_candidate_index(valid_catalog(), lexical_config())
    reversed_input = build_candidate_index(reversed_catalog(), lexical_config())

    assert isinstance(forward, CandidateIndexBuildSuccess)
    assert isinstance(reversed_input, CandidateIndexBuildSuccess)
    assert forward.members == reversed_input.members
    assert forward.manifest == reversed_input.manifest
    assert tuple(member.entry_type for member in forward.members) == (
        CandidateEntryType.PRODUCT_NAME,
        CandidateEntryType.APPROVED_ALIAS,
    )
```

- [x] **Step 2: 새 테스트가 success 타입 또는 build 결과 부재로 실패하는지 확인**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py::test_lexical_build_is_deterministic_across_input_order -q
```

기대 결과: `CandidateIndexBuildSuccess` 또는 lexical member build가 아직 없어 실패한다.

- [x] **Step 3: canonicalization과 hash helper 구현**

```python
def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()
```

구성원 payload는 공식 Product Identity, publication reference, entry type/reference, 표시 속성,
normalized text, Source Snapshot, Catalog와 normalization provenance를 명시적 null과 함께 포함한다.
구성원은 canonical bytes 기준으로 정렬한다.

- [x] **Step 4: identity·참조 무결성과 conflict 테스트 작성**

```python
def test_same_name_different_official_product_identities_remain_distinct() -> None:
    result = build_candidate_index(catalog_with_same_name_products(), lexical_config())
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert {member.product_identity.canonical_code for member in result.members} == {"P-001", "P-002"}


def test_orphan_alias_fails_without_members() -> None:
    result = build_candidate_index(catalog_with_orphan_alias(), lexical_config())
    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID
```

동일 identity의 상충 정의는 `DUPLICATE_PRODUCT_IDENTITY`, 동일 member key의 상충 content는
`MEMBER_CONFLICT`, conflicting approved alias는 `ALIAS_CONFLICT`로 분리한다. Ingredient alias,
미승인·비활성 alias와 HIRA identifier가 구성원에 들어오지 않는 테스트를 각각 둔다.

- [x] **Step 5: manifest count와 hash를 구현하고 golden assertion 추가**

```python
def test_manifest_records_lexical_provenance_and_reproducible_hashes() -> None:
    result = build_candidate_index(valid_catalog(), lexical_config())
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.index_kind is CandidateIndexKind.MEDICATION_CANDIDATE
    assert result.manifest.member_count == 2
    assert result.manifest.product_identity_count == 1
    assert result.manifest.product_name_count == 1
    assert result.manifest.approved_alias_count == 1
    assert result.manifest.vector_count == 0
    assert len(result.manifest.content_hash) == 64
    assert set(result.manifest.content_hash) <= set("0123456789abcdef")
    repeated = build_candidate_index(valid_catalog(), lexical_config())
    assert isinstance(repeated, CandidateIndexBuildSuccess)
    assert repeated.manifest.content_hash == result.manifest.content_hash
```

같은 fixture의 독립 반복 결과를 exact-match하고, Catalog version 또는 normalized text 한 필드를
변경하면 hash가 달라지는 negative assertion도 함께 추가한다. 테스트는 구현 내부 helper를 직접
호출하지 않는다.

- [x] **Step 6: Task 2 전체 검증**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py -q
uv run ruff check ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
uv run ruff format ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py --check
uv run mypy ai_worker/tasks/rag/candidate_index.py
```

- [x] **Step 7: Task 2 커밋**

```bash
git add ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
git commit -m "✨ feat: 결정적 Candidate Index manifest 생성"
```

## Task 3: HYBRID 임베딩 검증

**파일:**

- 수정: `ai_worker/tasks/rag/candidate_index.py`
- 수정: `ai_worker/tests/rag/test_candidate_index.py`

**인터페이스:**

- 소비: 정렬된 lexical 구성원과 `CandidateIndexBuildConfig`
- 생성: `CandidateEmbeddingPort.embed(entries, config)`와 vector가 결속된 build success

- [x] **Step 1: 정상 HYBRID embedding 테스트 작성**

```python
class FixedEmbeddingPort:
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        assert tuple(request.normalized_text for request in requests) == ("가나다정별칭", "가나다정")
        return tuple(
            CandidateEmbeddingVector(member_key=request.member_key, values=vector)
            for request, vector in zip(requests, ((1.0, 0.0), (0.0, 1.0)), strict=True)
        )


def test_hybrid_build_binds_vectors_to_sorted_members() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(dimension=2), FixedEmbeddingPort())
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert tuple(member.embedding for member in result.members) == ((1.0, 0.0), (0.0, 1.0))
    assert result.manifest.vector_count == 2
    assert result.manifest.embedding_model_version == "synthetic-model-v1"
```

- [x] **Step 2: embedding port 부재로 실패하는지 확인**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py::test_hybrid_build_binds_vectors_to_sorted_members -q
```

기대 결과: embedding protocol 또는 HYBRID 경로가 구현되지 않아 실패한다.

- [x] **Step 3: embedding protocol과 최소 HYBRID 경로 구현**

```python
class CandidateEmbeddingPort(Protocol):
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]: ...
```

구성원을 먼저 결정적으로 정렬한 뒤 `member_key + normalized_text` 요청 tuple을 한 번 전달한다. 응답의
`member_key` 순서가 요청과 exact-match하는지 확인한 뒤 vector를 결속하고 manifest hash를 다시 계산한다.

- [x] **Step 4: embedding failure matrix 작성**

count 부족·초과, dimension 불일치, `NaN`, `Infinity`, 잘못된 tuple 순서 witness와 port 미제공을
각각 `EMBEDDING_OUTPUT_INVALID`로 검증한다. failure의 `details`에는 field name만 있고 vector나 provider
원문 오류가 없는지도 assertion한다.

```python
@pytest.mark.parametrize("vector", [(float("nan"), 0.0), (float("inf"), 0.0), (1.0,)])
def test_invalid_embedding_fails_without_partial_output(vector: tuple[float, ...]) -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(dimension=2), SingleVectorPort(vector))
    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID
    assert not hasattr(result, "members")
```

- [x] **Step 5: Task 3 전체 검증 및 커밋**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py -q
uv run ruff check ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
uv run mypy ai_worker/tasks/rag/candidate_index.py
git add ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
git commit -m "✨ feat: Candidate Index 임베딩 무결성 검증"
```

## Task 4: 검색 port와 단계 순서·provenance 검증

**파일:**

- 수정: `ai_worker/tasks/rag/candidate_index.py`
- 수정: `ai_worker/tests/rag/test_candidate_index.py`

**인터페이스:**

- 입력: `CandidateSearchQuery`, `CandidateIndexManifest`, `CandidateIndexSearchPort`
- 출력: `CandidateIndexSearchSuccess` 또는 `CandidateIndexSearchFailure`
- 함수: `search_candidate_index(query, manifest, port)`

- [x] **Step 1: lexical 단계 호출 순서와 raw hit 보존 테스트 작성**

```python
def test_lexical_search_calls_stages_in_order_and_preserves_repeated_identity_hits() -> None:
    port = RecordingSearchPort(product_code="P-001")
    result = search_candidate_index(valid_query(limit=5), lexical_manifest(), port)

    assert isinstance(result, CandidateIndexSearchSuccess)
    assert port.calls == [
        CandidateSearchStage.PRODUCT_NAME_EXACT,
        CandidateSearchStage.APPROVED_ALIAS_EXACT,
        CandidateSearchStage.TRIGRAM_EDIT_DISTANCE,
    ]
    assert tuple(hit.stage for hit in result.raw_hits) == tuple(port.calls)
    assert [hit.product_identity.canonical_code for hit in result.raw_hits] == ["P-001"] * 3
```

- [x] **Step 2: 검색 타입과 함수 부재로 실패하는지 확인**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py::test_lexical_search_calls_stages_in_order_and_preserves_repeated_identity_hits -q
```

- [x] **Step 3: 검색 enum·타입·port와 orchestration 최소 구현**

```python
class CandidateSearchStage(StrEnum):
    PRODUCT_NAME_EXACT = "PRODUCT_NAME_EXACT"
    APPROVED_ALIAS_EXACT = "APPROVED_ALIAS_EXACT"
    TRIGRAM_EDIT_DISTANCE = "TRIGRAM_EDIT_DISTANCE"
    DENSE_VECTOR = "DENSE_VECTOR"


class CandidateIndexSearchPort(Protocol):
    def search_product_name_exact(
        self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_approved_alias_exact(
        self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_trigram_edit_distance(
        self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_dense_vector(
        self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
    ) -> tuple[CandidateRawHit, ...]: ...
```

`search_candidate_index(...)`는 query의 index version과 manifest를 먼저 exact-match하고 lexical 세 단계를
고정 순서로 호출한다. `HYBRID`일 때만 `DENSE_VECTOR`를 마지막에 호출한다. stage별 raw hit를 합치되
Product Identity 기준으로 dedupe하지 않는다.

- [x] **Step 4: HYBRID dense-last와 provenance failure matrix 추가**

```python
def test_hybrid_search_calls_dense_only_as_final_auxiliary_stage() -> None:
    port = RecordingSearchPort(product_code="P-001")
    result = search_candidate_index(valid_query(limit=5), hybrid_manifest(), port)
    assert isinstance(result, CandidateIndexSearchSuccess)
    assert port.calls[-1] is CandidateSearchStage.DENSE_VECTOR


@pytest.mark.parametrize("field", ["index_version", "catalog_version", "normalization_version"])
def test_hit_provenance_mismatch_fails_closed(field: str) -> None:
    result = search_candidate_index(valid_query(limit=5), lexical_manifest(), MismatchedHitPort(field))
    assert isinstance(result, CandidateIndexSearchFailure)
    assert result.reason is CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH
    assert result.raw_hits == ()
```

rank는 단계별 1부터 연속이어야 하고 score는 finite number여야 한다. retrieval limit은 `1 <= limit <=
manifest.candidate_limit`이어야 하며 모든 단계에 동일하게 전달한다. lexical manifest에서 port가 vector
hit를 반환하거나 HYBRID vector hit에 embedding version이 없으면 fail-closed한다.

- [x] **Step 5: 독립 Python process에서 DB 의존성 없는 import 검증 추가**

```python
def test_candidate_index_import_does_not_load_backend_or_database_modules() -> None:
    project_root = Path(__file__).parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ai_worker.tasks.rag.candidate_index; "
                "assert 'sqlalchemy' not in sys.modules; "
                "assert not any(name.startswith('backend.app') for name in sys.modules)"
            ),
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
```

이 테스트는 소스 문자열을 검사하지 않고 실제 import 부작용을 확인해 RAG-07A의 독립 실행 경계를
고정한다.

- [x] **Step 6: Task 4 전체 검증 및 커밋**

```bash
uv run pytest ai_worker/tests/rag/test_candidate_index.py -q
uv run ruff check ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
uv run ruff format ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py --check
uv run mypy ai_worker/tasks/rag/candidate_index.py
git add ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
git commit -m "✨ feat: Candidate Index 검색 단계 계약 추가"
```

## Task 5: 전체 회귀·범위·차단 상태 검증

**파일:**

- 수정 필요 시: `ai_worker/tasks/rag/candidate_index.py`
- 수정 필요 시: `ai_worker/tests/rag/test_candidate_index.py`
- 검토: `docs/designs/ceohwj/issue-167-rag-candidate-index-design.md`
- 검토: `docs/designs/ceohwj/issue-167-rag-candidate-index-implementation-plan.md`

**완료 기준:** 모든 local pure test와 static check가 통과하고, DB integration은 정확한 blocker로 보고되며,
변경 파일이 Issue #167 소유 경계 안에 있다.

- [x] **Step 1: RAG 단위 테스트 전체 실행**

```bash
uv run pytest ai_worker/tests/rag -q
```

- [x] **Step 2: Ruff·format·Mypy 실행**

```bash
uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
uv run ruff format ai_worker/tasks/rag ai_worker/tests/rag --check
uv run mypy ai_worker/tasks/rag
```

- [x] **Step 3: integration test 상태 확인**

```bash
test -f tests/integration/rag/test_candidate_index_query.py
```

기대 결과: RAG-06/RAG-07B 산출물이 아직 없으면 exit 1이다. 이를
`BLOCKED_BY_RAG_04_OR_06`으로 최종 보고하며 테스트 성공 수에 포함하지 않는다. 파일이 병렬 작업으로
새로 생겼다면 내용을 검토한 후 PostgreSQL 선행조건을 충족하는 환경에서 별도로 실행한다.

- [x] **Step 4: 금지된 변경과 민감정보 확인**

```bash
git diff --name-only origin/develop...HEAD
rg -n "API_KEY|PASSWORD|patient_name|raw_value|KNOWLEDGE_EVIDENCE|PUBLIC_TRACK_F=true" \
  ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
```

기대 결과: 변경 파일은 이 계획의 네 파일뿐이며, 두 Python 파일에 secret·실환자 값·Evidence index
혼합·공개 게이트 활성화가 없다. 계약상 금지 필드명을 검증하는 negative test 문자열만 발견되면 해당
줄이 assertion임을 직접 확인한다.

- [x] **Step 5: 전체 diff와 whitespace 검증**

```bash
git diff --check origin/develop...HEAD
git diff --stat origin/develop...HEAD
git status --short --branch
```

- [x] **Step 6: 검증 중 수정이 발생한 경우에만 마무리 커밋**

```bash
git add ai_worker/tasks/rag/candidate_index.py ai_worker/tests/rag/test_candidate_index.py
git commit -m "✅ test: Candidate Index 계약 회귀 보강"
```

수정이 없다면 빈 커밋을 만들지 않는다.

## 최종 보고 항목

- 생성·수정 파일과 각 책임
- 결정적 member/manifest hash 검증 결과
- Catalog fail-closed와 embedding failure matrix 결과
- 검색 단계 순서·raw provenance 검증 결과
- Ruff, format, Mypy, RAG unit test의 실제 통과 건수
- `tests/integration/rag/test_candidate_index_query.py` 실행 여부와
  `BLOCKED_BY_RAG_04_OR_06` 상태
- DB·migration·repository·API·환자 DTO 변경 0건
- `PUBLIC_TRACK_F=false` 유지

## 리뷰 보완: Catalog NFC와 Product-name 결속

- [x] NFC/NFD가 다른 저장 문자열인데 같은 member·manifest hash를 만드는 회귀 테스트를 RED로 확인
- [x] Catalog 전체 문자열을 자동 변환하지 않고 `CATALOG_TEXT_NOT_NFC`로 fail-closed
- [x] build config 문자열도 NFC가 아니면 `BUILD_CONFIG_INVALID`
- [x] `PRODUCT_NAME` Search Entry의 표시명·정규명을 Product row와 exact-match
- [x] 고정된 `display_limit=1` 검사 뒤의 도달 불가능한 중복 비교 제거
- [x] RAG-06 manifest hash canonicalization 책임과 단계별 raw hit limit 의미를 설계 문서에 명시
