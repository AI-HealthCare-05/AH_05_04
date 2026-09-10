# PD-398-M1 Source·Catalog 관리 경계

상태: Proposed / 관리 API·권한·감사 구현 및 PostgreSQL 검증 완료; 담당 리뷰 대기. 구현 담당 김지혜. 책임 리뷰: 송은영(Backend·권한), 정현우(Source·Catalog). 제품 수용: 권가빈. 기존 사용자 승인 범위에서 #398의 누락된 관리 기능을 구현하며, 담당 리뷰 전 current 승격·운영 공개하지 않는다.

## Decision

일반 API와 분리된 관리 FastAPI 프로세스가 단일 관리 Writer credential로 실행된다. 일반 API/Worker에 이 credential을 주거나 관리 Router를 mount하지 않는다. 관리 프로세스는 사용자 access token과 서버 저장 `source_catalog_manage` 권한을 확인한다. 가입·`is_admin`·payload는 권한을 만들지 않는다. 권한 부여/회수는 Backend one-shot 관리 코드로 처리한다. 권한 행 잠금으로 진행 중 변경과 회수를 직렬화하며, 기존 세션도 다음 요청부터 변경된 권한을 따른다.

## API와 변경 범위

`GET /management/{kind}/{id}`는 현재 revision/hash를 반환한다. `PATCH`와 `DELETE`는 expected_revision(최초 0), expected_hash(SHA-256), reason_code(CORRECTION/DUPLICATE/WITHDRAWAL), approval_hash(외부 승인 근거의 SHA-256), request_id(UUID)를 요구한다. actor는 검증된 access token에서만 가져온다. 추가 payload 필드는 거부한다. PATCH의 changes는 대상별 메타데이터 allowlist만 허용한다.

대상: source, endpoint, operation, snapshot 및 기존 catalog product/ingredient/alias/component. Source 계층은 DRAFT·DISABLED·PENDING이고 자식/사용 참조가 없는 자료만 변경·삭제한다. Snapshot 내용·checksum·version은 PATCH하지 않고 새 수집 version으로 정정한다. Snapshot DELETE는 PENDING·미사용·Receipt가 존재할 때만 허용한다. Catalog는 PENDING Snapshot·DISABLED Operation에 속하고 대상에 참조가 없는 자료만 수정·삭제한다. Source identity/FK, Catalog identity/FK/정규화 문자열 및 승인 상태는 변경할 수 없다. PR #372의 Identity/SearchEntry/Set은 후속 PR에서 이 경계에 연결한다. #404는 병합 후 추가 작업한다.

Catalog 수정은 표시용 보조 메타데이터로 제한한다. 정규화·검색 identity와 원본 canonical_checksum은 바꾸지 않는다. Source 원본 변경은 재수집·새 version·기존 승인 절차를 사용한다.

## Transaction과 감사

Dependency 사전 권한 검사 → transaction에서 권한 행 잠금·재검사 → Operation/Snapshot/대상 행 잠금 → revision/hash 비교 → 상태·모든 incoming FK 참조 검사 → 변경 → 변경 후 hash → 감사 INSERT → commit. 권한 실패는 성공 감사/변경을 남기지 않는다. 같은 actor/request_id와 같은 요청은 이전 결과를 재사용하고 다른 요청이면 409. 삭제 감사는 대상 FK를 두지 않아 자료 삭제 후에도 남는다.

revision은 해당 대상의 마지막 감사 revision이며 최초 0이다. hash는 모든 DB 컬럼의 정규 JSON SHA-256이다. 다른 쓰기 경로가 revision을 올리지 않아도 hash 비교가 덮어쓰기를 막는다. DELETE 후 revision/hash는 null. 감사에는 actor/당시 권한, before/after revision/hash, source_version/external_version/snapshot_id/canonical_checksum/receipt_hash, reason, 승인 근거 hash, request_id와 시각을 저장한다. Source 계층 메타데이터에는 Snapshot provenance가 존재하지 않아 null로 명시한다. Snapshot/Catalog에는 실제 Snapshot Receipt가 없으면 거부한다. external_version은 현행 모델에 없으므로 null이며 만들어내지 않는다. 승인 hash는 근거의 식별자이며 외부 문서의 진위를 자동 판정하지 않는다.

원문·변경 payload·URL·자유 입력 사유·사용자 PII는 감사에 저장하지 않는다. 감사 실패 시 전체 rollback. 401 인증 실패, 403 권한 없음, 404 대상 없음, 409 stale/사용 중/멱등 충돌/근거 없음, 422 허용되지 않은 필드. 일반 회원용 화면에 관리 기능을 추가하지 않는다.

## 배포 경계

새 migration은 관리 권한/append-only 감사 테이블만 생성한다. Trigger/RLS/업무 DB 함수는 추가하지 않는다. 관리 역할은 대상의 필요한 UPDATE 컬럼·DELETE, 감사 SELECT/INSERT, 권한 SELECT와 잠금용 컬럼 UPDATE만 가진다. 권한 자체 부여/회수·감사 수정/삭제 권한은 없다. Runtime Catalog UPDATE/DELETE 권한도 회수하며 기존 수집 INSERT는 유지한다. 실제 운영 적용은 별도 절차다.

## 검증 근거

`tests/integration/rag/test_source_management.py`: 관리 API/Service, 실제 제한 역할, 권한 부여·회수, stale, 멱등 재시도, 감사 실패 rollback, 동시 수정·삭제, Snapshot 상태와 Receipt, migration 및 감사 보존 downgrade 거부를 검증한다. 기존 role provisioning/head/관리 실행 설정 검사와 합계 26 passed (2026-09-10). 전체 migration 150 passed, 실제 Backend 이미지 검증 2 passed. Ruff 전체 검사·format 및 Backend/Worker 516개 파일 mypy 통과. 관리 실행 절차는 [인수 문서](../../testing/issue-398/management-handoff.md)를 따른다.
