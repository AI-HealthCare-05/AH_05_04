# #166 Catalog Identity·Alias·Search Entry DB 기반 검증

- 구현 revision: `166a7b8c9d0e`
- 검증 환경: Python 3.13 / 격리 PostgreSQL 17
- 상태: 스키마 기반 구현·검증 완료. #166 전체 DB 통합 완료가 아니다.

## 구현 범위

- Product·Ingredient 공통 안정 Identity를 `(entity_type, code_system, canonical_code)`로 유일하게 저장한다.
- Product·Ingredient의 공식 코드와 안정 Identity가 일치하도록 composite FK와 삽입 검사를 둔다.
- Alias 대상은 Snapshot별 Product/Ingredient row 대신 안정 Identity를 사용한다.
- Alias 관찰의 `source_snapshot_id`, 원문·정규화값, 출처, 검토 상태, 레코드 상태와 유효성을 보존한다.
- Alias의 exact btree와 `pg_trgm` GIN 검색 인덱스를 둔다.
- Search Entry는 Product publication과 안정 Identity를 함께 참조하고, Alias형이면 같은 Product Identity의 Alias를 참조한다.
- Product 이름 Entry와 승인 Alias Entry의 타입별 nullable 규칙과 정규화 문자열을 검증한다.
- 승인 Alias Entry는 승인·활성·유효 Product Alias만 허용한다.
- Component의 기존 동일 Snapshot composite FK는 유지한다.

Product와 Alias는 서로 다른 Snapshot에서 관찰될 수 있다. Alias 자체의 Source Snapshot은 유지하면서
대상은 안정 Identity로 결속한다. 이 허용을 Component의 동일 Snapshot 규칙으로 확대하지 않는다.

## 기존 데이터 이행

- 공식 코드가 있는 기존 Product·Ingredient는 결정적 UUID 형식의 안정 Identity로 backfill한다.
- 공식 코드가 없는 Ingredient는 이름으로 Identity를 만들지 않고 upgrade를 중단한다.
- 기존 Alias의 `is_approved`만으로 출처·검토 상태·활성 상태·유효성을 추정하지 않는다. 기존 Alias가
  하나라도 있으면 근거를 확인해 별도로 이행할 때까지 upgrade를 중단한다.
- 새 Alias 또는 Search Entry가 있으면 과거 Alias 대상과 승인 boolean을 복원할 수 없으므로 downgrade를 중단한다.
- 로컬 사전조사에서 기존 Catalog 네 테이블은 모두 0행이었지만, 다른 환경도 비어 있다고 가정하지 않는다.

## 검증 결과

```text
uv run pytest tests/migration/test_rag_source_catalog_migration.py -q
33 passed

uv run pytest backend/app/tests/rag/test_rag_source_catalog_repository.py -q
10 passed

MYPYPATH="$PWD/backend:$PWD" uv run mypy \
  backend/app/models/rag_catalog.py \
  backend/app/repositories/rag_source_catalog_repository.py
Success: no issues found in 2 source files

uv run ruff format --check <변경 Python 파일>
6 files already formatted

uv run ruff check <변경 Python 파일>
All checks passed!

uv run alembic -c backend/alembic.ini heads
166a7b8c9d0e (head)

git diff --check
통과
```

Migration 검증은 빈 DB upgrade, 기존 coded 구성원 backfill, 증명할 수 없는 Alias·Ingredient 변환 거부,
upgrade/downgrade 보호, 안정 Identity와 교차 Snapshot Alias, Search Entry identity·상태·문자열 결속,
기존 Source/Snapshot/Artifact 회귀를 포함한다.

## 이번 revision에서 확정하지 않은 범위

- D-02 `normalization_run_id`의 의미·물리 실행 구조와 Publication FK
- `rag_source_ingestion_run`을 정본 normalization 실행으로 대체하는 방식
- Alias/Crosswalk 불변 Set/member와 Catalog 구성 식별자
- export checksum, Catalog envelope hash, Candidate projection hash, Candidate Index manifest hash,
  Runtime medication Catalog manifest hash의 신규 물리 저장 구조
- 실제 Worker PostgreSQL adapter, 전체 적재 transaction, commit 결과 재조회와 실패 감사
- Runtime Bundle 활성화

현재 migration의 `down_revision=169b2c3d4e5f`는 작성 시점의 단일 head다. 합의된 선행
Evidence/Citation migration이 병합되면 PR 제출 전에 최신 head와 중복 변경을 다시 대조하고
`down_revision`을 재연결한다. 이 재연결 전 결과를 최종 migration 순서 증빙으로 사용하지 않는다.
