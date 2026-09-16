# PD-668 — Check-in 정정의 Track C 잠금 권한 분리

- 상태: Proposed / 사용자 수정 범위 확인, 구현·책임 리뷰 대상
- 구현 담당: 권가빈 (`hazelnutflavoured`)
- 단일 책임 리뷰어: 송은영 (`phina-io`) — Backend·DB·Security, Track B 정정과 Track C 무효화, Frontend 오류 영향
- 협업 영역: 김지혜의 Track C 저장 기반, 남한솔의 일정/리포트 소비. 이번 변경은 UI·API payload를 바꾸지 않는다.
- 범위 확인: 2026-09-16 요청자가 잠금 전용 컬럼과 최소 권한 방식 및 담당·리뷰어를 확인했다. 이 확인은 코드 리뷰 승인이나 Production 적용 승인이 아니다.

## 문제와 근거

운영 Check-in PUT의 HTTP 500에서 `permission denied for table safety_assessment`를 확인했다.
운영 Runtime의 safety_assessment·barrier_response·support_action_plan SELECT/UPDATE는 모두 false였다.
사용자 식별자·의료 기록·인증값·원문 로그는 이 문서에 포함하지 않는다.

합성 데이터와 실제 별도 Runtime credential로 UNCONFIRMED→NOT_TAKEN은 성공하고,
다음 NOT_TAKEN→TAKEN은 같은 권한 오류로 실패하는 것을 재현했다.
기존 Backend 테스트의 관리자 권한 DB 세션만으로는 이 배포 권한 누락을 찾을 수 없었다.

## 결정안

기존 `Check-in → Safety → Barrier → ActionPlan` 잠금 순서와 같은 transaction의 감사·무효화를 유지한다.
PostgreSQL SELECT FOR UPDATE에는 UPDATE 권한도 필요하므로 SELECT만 부여하는 수정은 충분하지 않다.
Safety·Barrier의 식별자나 의료·응답 내용에 UPDATE 권한을 주는 대신, 기존 Source 잠금 권한 분리 선례처럼
각 테이블에 `checkin_lock_marker INTEGER NOT NULL DEFAULT 0 CHECK (checkin_lock_marker = 0)`를 추가한다.
Runtime에 SELECT와 해당 컬럼의 UPDATE만 부여한다. 애플리케이션은 marker 값을 변경하지 않는다.
Plan에는 SELECT와 UPDATE(status, cancelled_at)만 부여한다. INSERT·DELETE·TRUNCATE·snapshot 변경은 허용하지 않는다.
Source Writer 또는 PUBLIC으로 권한을 확장하지 않는다.

기존 행은 marker 0으로 보존하며 새 forward migration을 사용한다. 과거 migration은 수정하지 않는다.
권한 provisioning은 migration 다음에 수행한다. Downgrade는 명시적으로 거부하고 reviewed forward-fix를 사용한다.
일반 CHECK와 기존 Python Service/Repository만 사용하며 Trigger·RLS·DB 업무 함수는 추가하지 않는다.

## 범위 및 한계

이 수정은 Track B 정정에 필수인 Track C 무효화만 복구한다. Track C 신규 응답/Plan 생성/완료/Follow-up 권한을
포괄적으로 열지 않는다. 공개 gate, 동의, Source·의료 승인 및 API 의미는 그대로다.
컬럼 권한은 피해 범위를 제한하지만 raw SQL에서 상태 전이·소유권 검증을 대신하지 않는다.
기존 Python 서비스가 SELF 소유권·revision·현재 ACTIVE 상태를 검사한다.

## 검증·정본

- [Proposed 계약](../../contracts/proposed/checkin-runtime-lock-v1.md)
- [검증 기록](../../validation/track-b/issue-668-checkin-runtime-lock.md)
- 실제 Runtime credential의 정정, 빈/기존 Track C 이력, ACTIVE만 취소, 감사 보존 및 권한 거부를 검증한다.
- 책임 리뷰 승인·merge queue·배포 후 검증은 별도이며 이 Decision을 Current나 외부 공개 승인으로 취급하지 않는다.
