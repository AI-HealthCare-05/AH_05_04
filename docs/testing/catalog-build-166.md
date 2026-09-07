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
