# Check-in 정정 Runtime 잠금 권한 v1 — #668

- 상태: **Proposed — 구현 및 책임 리뷰 대상**
- 구현 담당: 권가빈. 단일 책임 리뷰어: 송은영 (Backend·DB·Security·Track B/C 무효화 및 Frontend 소비 영향)
- 근거: [PD-668](../../governance/decisions/2026-09-16-checkin-runtime-lock-668.md)
- 기존 계약: [Track C 저장](track-c-storage-v1.md), [Check-in target](../targets/post-mvp-1/checkin-v1.md)

## 추가 저장 필드

`safety_assessment`와 `barrier_response` 각각에 `checkin_lock_marker`를 추가한다.
타입 INTEGER, NOT NULL, server default 0, 허용값은 CHECK로 0만 허용한다.
기존 행과 신규 행의 값은 0이며 의미 있는 업무 데이터·revision·의료 판정은 담지 않는다.
ORM에는 같은 default/requiredness/constraint를 선언한다. API/DTO에는 노출하지 않는다.

## Runtime 권한

| 테이블 | 허용 | 허용하지 않음 |
| --- | --- | --- |
| safety_assessment | SELECT, UPDATE(checkin_lock_marker) | INSERT, 업무 필드/ID UPDATE, DELETE, TRUNCATE |
| barrier_response | SELECT, UPDATE(checkin_lock_marker) | INSERT, 업무 필드/ID UPDATE, DELETE, TRUNCATE |
| support_action_plan | SELECT, UPDATE(status, cancelled_at) | INSERT, 나머지 필드 UPDATE, DELETE, TRUNCATE |

SELECT FOR UPDATE에 필요한 컬럼 UPDATE를 marker에만 부여하며 marker 변경은 하지 않는다.
marker를 1이나 NULL로 변경하면 일반 CHECK/NOT NULL 제약이 거부한다.
Source Writer/PUBLIC에는 위 권한을 부여하지 않는다. 재provision 시 기존 불필요한 table/column 권한을 회수한 뒤
같은 최소 권한을 부여한다. 새 Track C 생성·Follow-up API의 공개 권한 정책은 범위 밖이다.

## 기존 정정 의미 보존

NOT_TAKEN 정정은 기존 소유권/revision 검사와 감사 생성 후 같은 transaction에서 Safety·Barrier·ACTIVE Plan을
기존 순서로 잠근다. 이전 Check-in revision의 ACTIVE Plan만 CANCELLED로 전환하며 취소 시각을 기록한다.
Safety·Barrier 이력과 COMPLETED/CANCELLED Plan은 변경하지 않는다. 실패하면 정정·감사·무효화 전체를 rollback한다.
UNCONFIRMED 정정과 최초 기록, API 경로·요청/응답·오류 의미는 바꾸지 않는다.

## Migration·검증

Migration `668a1b2c3d4e`는 `633a1b2c3d4e` 이후 forward 적용한다. 기존 데이터는 삭제/재작성하지 않는다.
Migration 후 role provisioning, 이후 앱 기동 순서를 따른다. Downgrade는 항상 0인 marker와 해당 CHECK만 제거하며 업무 이력은 보존한다. 운영 복구는 reviewed forward-fix를 사용한다.
[실제 PostgreSQL 검증](../../validation/track-b/issue-668-checkin-runtime-lock.md)을 통해 합성 데이터만으로 확인한다.
Current 승격은 구현·migration·테스트·지정 리뷰 승인 증빙이 갖춰진 구현 PR에서만 수행한다.
