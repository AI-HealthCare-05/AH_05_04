# #398 Catalog Python 무결성 전환 — Draft PR #372

상태: Draft PR #372 병합 전 전환 범위. 해당 PR의 최신 코드가 바뀌면 다시 대조한다.

## 대상

Draft PR #372가 추가하는 `166a7b8c9d0e`와 `166b8c9d0e1f` migration의 PostgreSQL 함수 5개와 사용자 Trigger 15개를 #398 제거 범위에 포함한다.

- Product·Ingredient Identity 자동 생성·결속: 함수 1개, Trigger 2개
- Search Entry의 Product·Alias 정합성: 함수 1개, Trigger 1개
- Catalog Set 자식의 생성 transaction 결속: 함수 1개, Trigger 3개
- Set에 포함된 Catalog 구성원 변경 차단: 함수 1개, Trigger 5개
- Catalog Set·Source·Member·Hash 변경 차단: 함수 1개, Trigger 4개

PR #372는 2026-09-10 확인 시 `develop`보다 28개 commit 뒤에 있으며 migration 부모가 `164b6c7d8e9f`다. 그대로 병합하면 현재 `398f60718293`과 별도 Alembic head가 생기므로 최신 `develop` 위에서 migration 순서를 다시 연결해야 한다.

## Python 대체 기준

### Identity 결속

- Adapter가 공식 `entity_type`, `code_system`, `canonical_code`로 Identity를 명시적으로 조회하거나 생성한다.
- 같은 transaction에서 Product·Ingredient의 `entity_identity_id`를 저장하고 저장 결과를 다시 읽어 요청 값과 비교한다.
- 동시 생성은 자연키 UNIQUE와 충돌 후 재조회로 하나의 Identity에 수렴시킨다.
- `NOT NULL`, 복합 FK와 공식 Identity 중복 방지 UNIQUE는 유지한다.

### Search Entry

- Product와 선택된 Alias를 transaction 안에서 잠그거나 동일 Catalog build의 고정 입력으로 검증한다.
- Product 상태, Identity 일치, Alias 승인·활성·유효 상태와 정규화 문자열 일치를 Python에서 확인한다.
- Search Entry 저장과 Catalog Set 조립이 실패하면 전체 build를 rollback한다.
- FK·CHECK·UNIQUE와 trigram index는 유지한다.

### Catalog Set 조립과 불변성

- Set·Source·Member·Hash는 `save_build()`가 소유한 하나의 transaction에서 저장한다.
- Trigger용 `assembly_xid`를 추가하지 않는다.
- 저장 직후 Source 수, Member 수, Hash 수와 canonical bytes·digest를 다시 읽어 전체 plan과 대조한다.
- 이미 존재하는 envelope는 저장된 전체 구성을 재검증한 뒤에만 멱등 성공으로 처리한다.
- 게시된 Set과 구성원은 UPDATE로 바꾸지 않고 새 Set을 생성한다.
- Runtime 계정은 Catalog를 읽기만 하며, 검토된 Catalog Writer만 필요한 INSERT를 수행한다. UPDATE·DELETE·TRUNCATE는 부여하지 않는다.

## 병합 순서

1. PR #372를 최신 `develop`에 맞춰 갱신하고 Alembic 부모를 단일 최신 head에 연결한다.
2. 두 신규 migration에서 함수·Trigger 생성과 `assembly_xid`를 제거한다.
3. Catalog Adapter에 Identity·Search Entry·Set 검증과 단일 transaction 저장을 구현한다.
4. 새 Catalog 테이블의 Writer를 정적 허용 목록과 운영 역할 정책에 최소 권한으로 연결한다.
5. PR #372의 Trigger 존재·DB 예외 테스트를 Python 검증·rollback·동시성·권한 거부 테스트로 바꾼다.
6. 빈 DB 전체 migration, 기존 Catalog 자료 migration, 최종 사용자 Trigger/RLS/제거 함수 0개를 검증한다.

현재 #398 브랜치는 PR #372의 이동 중인 5천 줄 이상 diff를 선반영하지 않는다. 대신 새 함수 이름을 최종 제거 목록에 등록하고, 새 Catalog 테이블은 승인 Writer가 없는 보호 대상으로 등록해 미완료 상태가 CI를 통과하지 못하게 한다.

## #398 이슈 본문 반영 문구

`Migration 처리`의 미병합 #166 문장은 다음과 같이 구체화한다.

> 아직 병합되지 않은 #166의 Draft PR #372 migration은 Trigger·PL/pgSQL 함수와 `assembly_xid` 생성 코드를 직접 제거하고 위 Python 대체 경로로 교체한 뒤 병합합니다. PR #372를 최신 develop에 맞춰 갱신해 Alembic 단일 head를 유지하며, 이동 중인 draft 코드를 #398 브랜치에 선반영하지 않습니다.

`관련 PR`에는 다음 항목을 추가한다.

- [Draft PR #372](https://github.com/AI-HealthCare-05/AH_05_04/pull/372): #166 Catalog Identity·Search Entry·Catalog Set PostgreSQL 적재. 함수 5개와 Trigger 15개의 Python 전환 선행 대상.
