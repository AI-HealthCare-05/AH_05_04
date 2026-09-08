# Issue #166 Catalog build 검증 기록

## 구현된 범위

- 공식 제품·성분 Identity는 `entity_type + code_system + canonical_code`로 구성한다.
- Product와 Ingredient에 Source Snapshot과 Source record key를 보존한다.
- Product Component 자연키는 `(product, ingredient, component_role)`로 유지한다.
- 원문 표시 문자열과 검색용 정규화 문자열을 분리한다.
- 승인되고 활성 상태이며 효력이 있는 Product Alias만 Search Entry로 만든다.
- 비활성 Product, Ingredient Alias, 미승인·비활성·효력 없는 Alias는 실행용 검색 항목에서 제외한다.
- HIRA code system은 P0 Product·Ingredient Identity 및 Search Entry에서 제외한다.
- 중복 공식 Identity, 고아 Component, 여러 활성 Product를 가리키는 동일 정규화 Alias를 저장 전에 차단한다.
- 검증에 실패한 Catalog는 저장 포트를 호출하지 않고 manifest와 JSONL도 만들지 않는다.
- 구성원 입력 순서와 무관하게 canonical JSONL, export SHA-256, manifest SHA-256을 재현한다.
- Candidate Index에는 `CandidateCatalogExport` 타입과 manifest hash를 인계한다.

## 검증 결과와 고정 실패 코드

| 검증 | 결과 | 실패 코드 |
| --- | --- | --- |
| Product 공식 Identity 중복 | 활성 후보 차단 | `DUPLICATE_PRODUCT_IDENTITY` |
| Ingredient 공식 Identity 중복 | 활성 후보 차단 | `DUPLICATE_INGREDIENT_IDENTITY` |
| Product 또는 Ingredient가 없는 Component | 활성 후보 차단 | `REFERENTIAL_INTEGRITY_INVALID` |
| 동일 Alias가 서로 다른 활성 Product를 가리킴 | 자동 병합 없이 활성 후보 차단 | `ALIAS_CONFLICT` |
| 같은 Component 자연키 또는 같은 대상 Alias의 상충 내용 | 활성 후보 차단 | `MEMBER_CONFLICT` |

실패 상세에는 Source 원문이나 Alias 문자열을 기록하지 않고 안정적으로 생성된 구성원 참조만 기록한다.

## 결정적 산출물

- Schema version: `medication-catalog-v1`
- Manifest: `docs/validation/rag/catalog/synthetic-catalog-v1/manifest.json`
- Canonical JSONL: `docs/validation/rag/catalog/synthetic-catalog-v1/catalog.jsonl`
- JSON은 UTF-8, key 정렬, compact separator, LF 종료 규칙을 사용한다.
- manifest hash는 자기 자신을 제외한 manifest 의미 값의 canonical bytes로 계산한다.
- export checksum은 `catalog.jsonl` 전체 bytes의 SHA-256이다.

## 저장 경계

`CatalogBuildRepository.save_build()`는 Catalog 구성원과 manifest candidate를 호출자가 소유한 하나의
transaction에 저장하는 포트다. 검증 성공 후 이 메서드가 한 번만 호출되며, 저장 예외는 성공 결과로
변환하지 않는다.

현재 #164 DB 기반에는 다음 필드와 관계가 없어 손실 없는 Worker DB adapter를 구현할 수 없다.

- Catalog build/version과 manifest candidate 저장 구조
- Catalog build와 여러 Source Snapshot/version의 lineage
- Candidate용 Search Entry 저장 구조
- Alias의 `alias_source`, `review_status`, `status`, `is_effective`
- Alias Source Snapshot과 대상 Product/Ingredient Snapshot의 분리

따라서 실제 PostgreSQL 저장은 `BLOCKED_BY_WORKER_DB_ADAPTER`로 유지한다. 위 구조의 DB 계약과
migration이 승인되면 `CatalogBuildRepository` 구현체와 PostgreSQL commit/rollback 통합 테스트를 연결한다.
현재 구현은 Backend ORM model을 Worker에서 import하지 않는다.

## 실행 결과

```text
uv run pytest ai_worker/tests/rag/catalog -q
47 passed

uv run pytest ai_worker/tests/rag -q
711 passed

uv run ruff check ai_worker/tasks/rag/catalog ai_worker/tests/rag/catalog
All checks passed

MYPYPATH="$PWD/backend:$PWD" uv run mypy ai_worker/tasks/rag/catalog
Success: no issues found in 7 source files
```

## 남은 통합 조건

- #323 병합 후 승인된 Worker DB adapter 기준으로 branch를 갱신한다.
- 위 DB 계약과 migration을 DB 담당자에게 확인한다.
- 실제 PostgreSQL에서 성공 commit과 주입된 실패 rollback 시 부분 Catalog/manifest가 0건인지 검증한다.
- Source/Catalog approval과 Candidate Index·Resolver 평가 전 Runtime activation은 수행하지 않는다.

저장소 전체 테스트에서는 DB 비의존 테스트 `3179 passed, 10 skipped`까지 진행됐고, PostgreSQL migration
17건은 호스트 실행 환경에서 Compose 내부 hostname `postgres`를 해석하지 못해 실패했다. 로컬 DB를
대상으로 한 migration 재실행은 downgrade/upgrade와 trigger 변경이 기존 데이터를 바꿀 수 있어 수행하지
않으며, 격리된 CI PostgreSQL에서 확인한다.

## 2026-09-08 계약 결정과 독립적인 계산 계층 보완

PR #329의 `0740402eedd5b07188d4a123cc15108f40171c02`를 기준으로 다음 구현만 보완했다.

- 완전히 같은 구성원 행의 중복 검사를 리스트 순회에서 행 전체를 키로 하는 사전 조회로 변경했다.
  최초 등장 순서와 상충 행을 보존한다. Identity 또는 reference만으로 행을 합치지 않는다.
  Product에서 만들어진 기존 Entry 목록도 보존하고, Alias Entry 추가 시 중복 조회만 집합을 사용한다.
- `target_source_snapshot_id`는 `None`일 때만 Alias 자체 Snapshot으로 기본 설정한다.
  명시적인 빈 문자열·공백은 기존 필수 문자열 검증으로 거부한다. 유효한 교차 Snapshot 참조는 유지한다.
- 최초 등장 순서, 동일 reference의 상충 행, 서로 다른 원본 키, 기본값과 빈 입력에 대한 회귀 테스트를 추가했다.

합성 입력으로 기존 코드와 수정 코드의 구성원 전체를 비교했다. 기존 fixture와 서로 다른 제품
1,000개/5,000개를 각각 두 번 입력한 경우 결과가 같았다. 한 로컬 실행에서 2,000개 입력은
약 0.090초 → 0.023초, 10,000개 입력은 약 1.884초 → 0.118초였다. 측정은 제품 생성 이후
`build_catalog_members` 호출 시간을 비교한 참고값이며 성능 보장이나 운영 부하 검증이 아니다.

검증 명령은 기존 Python 3.13 테스트 환경에서 실행했다.

```text
python -m pytest ai_worker/tests/rag/catalog -q
54 passed

python -m pytest ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation -q
2004 passed, 8 skipped

ruff check .
All checks passed

ruff format . --check
509 files already formatted

MYPYPATH="$PWD/backend:$PWD" python -m mypy backend/app ai_worker
Success: no issues found in 435 source files
```

기존 export golden hash 테스트도 통과했다. DB·migration·외부 Provider 변경은 없으며,
PostgreSQL/Redis를 포함한 전체 CI runner는 이번 로컬 검증에 포함하지 않았다.
승인 상태/receipt·Ingredient 입력·의미상 Alias dedupe·service 실패 응답에 관한 기존 리뷰와
#164/#165/#166 공유 계약 결정은 별도 후속 범위로 남아 있다. 이 보완은 해당 리뷰의 해결이나
Runtime 활성화 승인을 의미하지 않는다.

## #319 반영 후 CI multiple heads 해결

#329에 Evaluation revision과 기존 Source Artifact revision이 별도 head로 남아
`alembic upgrade head`가 실패했다. #323의 검증된 최신 커밋 `55e2aff`를 병합하여
Evaluation → Source Artifact → Receipt/FAILED 재시도 → Verification 보호 순서를 반영했다.
단일 head는 `165d7e6f5041`이며, #323의 Source 리뷰 보완도 함께 상속했다.

- 격리 PostgreSQL 17 빈 DB에서 `upgrade head` 성공
- 전체 migration 테스트: **66 passed**
- Catalog·Source ingestion·Governance Receipt 테스트: **334 passed**
- PostgreSQL Source lifecycle·Evaluation repository 테스트: **16 passed**
- 전체 Ruff·서식 검사 통과 (**518 files**), Mypy **441개 소스 파일 통과**
- 이 수정은 Catalog 승인 receipt·Ingredient 입력·Alias 의미상 dedupe 등 기존 #329 리뷰의 해결을 의미하지 않는다.

## #329 RAG 변경 요청 반영 v2

- 독립 Ingredient registry를 입력으로 추가하고 Component의 암묵적 성분 생성을 제거했다. 기존 복합제 fixture 전체(Component 3개·Ingredient 2개)가 서비스 validate/export를 통과하며, 누락 Ingredient는 저장 없이 REJECTED다.
- 같은 제품의 반복 Alias는 provenance 행을 보존하고 검색 항목만 결정적으로 선택한다. 같은/다른 Snapshot 반복·입력 순서 반전과 다른 제품의 Alias 충돌을 각각 검증한다.
- Product/Ingredient/Alias 대상 누락 mapping 오류는 REFERENTIAL_INTEGRITY_INVALID로 서비스 REJECTED 처리한다.
- 승인 verifier가 없거나 Source 미승인·STALE이면 NOT_APPROVED이며 Candidate Index가 거부한다. 합성 승인 경로, 잘못된 checksum/Source receipt 거부, gate 변조 및 manifest exact recomputation을 검증한다.
- schema와 manifest는 v2로 갱신했다. 기존 합성 manifest의 APPROVED/CURRENT 자동 부여를 제거하고 새 golden으로 바꿨다. 파일 바이트 checksum과 승인 envelope hash는 별개다.
- 실제 승인 adapter와 정본 projection hash 최종 대응·DB provenance는 후속 범위다. 상세 계약과 결정 기록은 Catalog build v2 문서를 따른다.

## 2026-09-08 리뷰 보완 및 develop 병합 검증

리뷰 보완 커밋 `b8fa5e8` 이후 develop `1adf081`의 #328(Evaluation publish 파일 소유권 보호), #331(Frontend 재접속 복원), #334(Snapshot 최소 구현 deviation 문서)를 반영했다. 세 PR은 migration을 추가하지 않았으며 기존 Source migration을 보존했다. #334 문서 병합은 #164의 정규 normalization/provenance DB 구현 완료를 의미하지 않는다.

- Catalog 단위·서비스·Candidate 인계 회귀: **68 passed**
- 병합 후 Worker core/OCR/RAG/Evaluation 및 Source governance receipt: **2,152 passed, 8 skipped**
- PostgreSQL 17 빈 DB에서 `alembic upgrade head` 성공; 단일 head **165d7e6f5041**
- PostgreSQL 전체 migration·rollback 회귀: **66 passed**
- PostgreSQL Source lifecycle·Evaluation repository 통합: **16 passed**
- Ruff 및 서식: **520 files 통과**, Mypy: **443 source files 통과**
- Frontend(Node 24.19.0, 테스트용 VITE_API_BASE_URL 설정): **259 passed**, build·lint 통과
- `git diff --check` 및 병합 staged diff 검사 통과

Frontend 최초 실행은 테스트 API URL 미설정으로 실패했고, fixture가 요구하는 localhost URL을 설정한 재실행에서 전체 통과했다. 사용자 DB 대신 임시 PostgreSQL 컨테이너만 사용했다. Redis를 포함한 전체 CI runner는 로컬에서 실행하지 않았으며 원격 CI 결과는 별도로 확인해야 한다.
