# #398 Source·Catalog 관리 인수 절차

상태: 로컬 구현·검증. AWS/운영 DB 적용 아님. 책임 리뷰는 [PD-398-M1](../../contracts/proposed/source-catalog-management-398.md)을 따른다.

## 실행 순서

1. 기존 migration owner로 단일 head `3983a4b5c6d7`까지 적용하고 `scripts/ci/verify_database_head.py`로 사용자 Trigger/RLS/제거 함수 0개를 확인한다. 이미 적용한 migration 파일은 변경하지 않는다.
2. 관리 기능을 사용할 때만 별도 `SOURCE_MANAGEMENT_USER`, `SOURCE_MANAGEMENT_PASSWORD`를 보안 설정에 등록한다. 기존 Admin/Migration/Runtime/Source Writer와 모두 다른 로그인 역할이다. 예시 환경 파일은 비워 두며 일반 API·Worker에 전달하지 않는다.
3. `source-management-bootstrap` one-shot 서비스로 로그인 계정만 준비한다. 역할 충돌은 거부하고 테이블 쓰기 권한은 부여하지 않는다. 기존 역할의 비밀번호를 자동 변경하지 않는다.
4. `provision-db-roles`를 실행해 검증된 명시 권한을 적용한다. `SOURCE_MANAGEMENT_USER`가 설정되면 관리 역할 정책도 적용한다. Runtime의 Catalog UPDATE/DELETE는 회수하며, 기존 수집 INSERT는 유지한다. 일반 API에서 직접 Source 쓰기는 계속 차단된다.
5. `source-management-permission` one-shot 명령으로 `grant --user-id <UUID> --actor-id <operator UUID> --approval-hash <SHA-256> --request-id <UUID>`를 실행한다. 이 프로세스만 Migration credential을 사용한다. 회수는 동일 인수와 새 request-id로 `revoke`를 실행한다. 운영자 ID와 승인 hash는 실제 승인 기록을 사용하며 임의 샘플을 쓰지 않는다.
6. `source-management` 프로필을 명시적으로 실행한다. host의 `127.0.0.1:8010`에만 연결하고 Nginx·일반 회원 화면에는 경로를 추가하지 않는다. 기존 서비스와 동일한 JWT 서명키가 필요하다. 관리 앱은 시작 시 실제 DB 역할의 소유권/관리 권한/멤버십/테이블·컬럼 쓰기 권한을 검증한다.
7. 승인 사용자 access token으로 GET한 revision/hash를 PATCH/DELETE에 전달한다. 요청 본문은 [계약](../../contracts/proposed/source-catalog-management-398.md)을 따른다. 삭제는 대상 자료만 삭제하고 감사 기록은 보존한다.

관리 기능이 필요 없으면 관리 프로필을 실행하지 않는다. 서비스는 자동 배포·재시작 의존성에 추가하지 않았다. 관리 감사 migration `3980718293a4`의 과거 downgrade는 감사 잠금·존재 확인으로 기록 삭제를 거부한다. 최신 seal migration `398293a4b5c6`은 삭제 보호를 유지하기 위해 downgrade 자체를 거부한다. 운영 rollback은 audit 보존 계획과 담당 검토를 전제로 한다.

## 허용 범위와 후속 연계

DRAFT·미참조 Source 메타데이터와 PENDING·미사용 Snapshot에 속한 기본 Catalog의 제한된 보조 메타데이터만 수정한다. 이름 필드는 현행 NFC/공백 정규화 결과가 기존 identity와 같아야 한다. 원본·검색 identity·FK·version·checksum·승인 상태 변경은 새 version/기존 승인 절차로 처리한다. Snapshot은 관리 DELETE만 지원하고 원본 PATCH는 거부한다. Snapshot/Catalog의 실제 Receipt가 없으면 거부한다.

`#398` 먼저 병합 → `#291`의 입력·정보 계약에 맞춰 `#372` 진행. #372에는 이미 `c263698`(Trigger 제거), `7e1c113`(Python 검증), `6f1acc9`(develop 통합)이 반영되어 있지만, #398 병합 후 migration 부모와 신규 Catalog Writer 정책을 다시 연결해야 한다. 이 문서가 #372 병합 준비 완료를 뜻하지 않는다. #404의 병합본은 PR #429 리뷰 수정에 포함했다. 인증 테이블은 Runtime의 제한된 컬럼 권한으로 연결하며 Source/관리 Writer에는 열지 않는다.

## 검증

`tests/integration/rag/test_source_management.py`에서 전용 disposable PostgreSQL DB를 생성·삭제하며 API/Service/실제 역할과 migration을 검사한다. `tests/contract/test_source_management_deployment.py`는 관리 credential·Router·프로필 분리를 검사한다. 실환자 자료, Source 실응답, 운영 자격 증명은 fixture와 감사에 넣지 않는다.

## PR #429 추가 인수 조건

검증된 Snapshot은 일반 FK·CHECK·삭제 불가 Verification 이력으로 직접 DELETE를 차단한다. 미검증 PENDING만 기존 관리 경로로 삭제하며 상태·승인 값을 SQL로 우회해 바꾸지 않는다. 처방 재시도는 보존기간 내 최초 성공 응답을 재현하므로 최신 상태가 필요하면 GET을 사용한다. 배포 순서는 migration → verify-db-head → provision-db-roles → 서비스 시작으로 고정한다. 정적 SQL/AST 검사는 보조 수단이며 실제 DB 역할과 통합 테스트 증빙을 함께 확인한다.
