# #423 Schedule Audit DB 구현 검증 기록

상태: **DB 저장 구현 · 지정 리뷰/머지 대기** (2026-09-10).
기준 develop: `c97f20f7` (#429의 DB trigger 제거 포함).
브랜치: `feat/423-schedule-audit`. 구현 담당 권가빈; 책임 리뷰 송은영(Backend·DB·Security),
남한솔(Frontend 소비 의미). 본 기록은 책임 리뷰나 외부 공개 승인이 아니다.

## 승인·문서 반영

선행 문서 PR은 [#424](https://github.com/AI-HealthCare-05/AH_05_04/pull/424)이고 #423은 후속 DB 이슈다.
[Decision 승인표](../governance/decisions/2026-09-10-track-b-schedule-contract.md)에 두 책임 리뷰어의
APPROVED review URL·ID·commit·UTC 시각을 연결했다. 승인 대상 commit은
`4fdecc8af73a62803cd970160886115d0c91be36`이다. 계약을 Proposed에서 Approved target으로
이동하고 인덱스·Check-in 목표의 reason/INACTIVE/transaction 참조를 정렬했다.
전체 API·알림·Frontend 구현과 책임 리뷰 승인 전이므로 `current/`로 승격하지 않는다.

## 저장 구현

- `3984b5c6d7e8` 뒤 단일 head `423a1b2c3d4e`: 감사 테이블 및 nullable occurrence `cancelled_at`.
- 기존 일정의 현재 상태는 별도 접근 제한 JSON baseline에 보존하며 과거 actor/time/audit을 만들지 않는다.
  알 수 없는 기존 CANCELLED 취소 시각은 null로 유지한다. 감사 또는 취소 이력이 있으면 downgrade를 거부한다.
- FK RESTRICT·revision step·unique·actor 제약과 명시적 snapshot 검증.
- #398 기존 역할 provisioning에 감사 테이블 등록: Runtime SELECT/INSERT만 허용,
  UPDATE/DELETE/TRUNCATE 거부, Source Writer 접근 거부. 새 trigger·RLS·DB 함수·ORM event 없음.
- 표별 순차 잠금, Scheduler 한 실행에서 잠근 schedule 집합 재사용.
- PUT 생성·변경·재활성화, PATCH 취소·반복 취소 no-op, Scheduler 종료 revision+audit.
- 미래 PENDING·기한 유효·Check-in 없음 조건의 취소와 취소 시각 기록.
- USER audit changed_at보다 과거인 occurrence의 rolling 재생성 방지.
- Service savepoint로 실패한 mutation을 복원하며 외부 transaction의 commit은 호출자가 소유한다.

Migration은 새 테이블에 상속된 기본 ACL을 회수한다. 운영 upgrade 이후 기존 역할 provisioning을
재실행해야 Runtime 감사 쓰기가 가능하다. 실제 운영 migration·데이터 export·역할 변경은 수행하지 않았다.
Baseline 접근·보존·실패 재시도는 [PD-423](../governance/decisions/2026-09-10-schedule-audit-storage.md)을 따른다.

## 검증

전용 PostgreSQL 17·Redis 7과 합성 fixture를 사용한다. 실제 Runtime/Writer 역할 검증은 별도 테스트
DB와 임시 역할로 실행했다. 2026-09-10 최종 결과:

- `uv run --no-sync ruff check .`, `ruff format . --check`: 통과 (684 files).
- `uv run --no-sync mypy backend/app ai_worker`: 통과 (538 source files).
- `scripts/ci/run_test.sh`: 통과. Migration 161 passed; Backend·Contract·PostgreSQL 1,734 passed/59 skipped;
  Redis 통합 23 passed; Worker 2,713 passed/8 skipped. 합계 **4,631 passed / 67 skipped**, 통합 coverage 93%.
- 실제 역할 provisioning 테스트 통과 (`ISSUE398_TEST_POSTGRES_CONTAINER` 지정).
- DB 로직 재도입·보호 테이블 write 검사 통과. 최종 단일 head `423a1b2c3d4e`;
  카탈로그 검사에서 **Trigger/RLS/제거 대상 함수 0개** 확인.
- 변경 Markdown 8개 HTML render·내용 검토 및 상대 파일 링크 검사, 전체 diff·`git diff --check` 통과.


검증 범위:

- migration upgrade·baseline 없는 기존 DB의 무변경 실패·0600 artifact·덮어쓰기 거부·기존 null 보존·downgrade guard.
- 생성/취소/no-op/재활성화 감사 chain·time 보존, stale revision, SELF·과거 version 거부.
- 동시 Scheduler 종료/Scheduler·종료/PUT의 단일 revision chain, 기존 처방 정정 동시성 회귀.
- rolling 생성 하한, Check-in 이력과 정확한 취소 경계 보존.
- 감사/알림 실패 rollback, 공통 SyncMutationIdempotencyService의 성공 재현 및 snapshot 용량 실패 rollback.
- SQL 합성 notification adapter의 같은 session 취소와 실패 시 일정·감사·알림 원자성.
- Runtime 직접 SQL UPDATE/DELETE/TRUNCATE 거부 및 재-provisioning 후 기존 권한 회수.

## 후속 연동 경계

#202의 일정 API 호출자는 동일 transaction에서 멱등 성공 응답을 먼저 재현하고 신규 mutation의
암호화 snapshot 저장 후 commit해야 한다. 공개 DTO 변환·frequency_per_day 검증 및 저장 예외의
기존 404/409 매핑은 해당 API가 담당한다. 이 변경은 공개 route/DTO·Frontend를 추가하지 않는다.

#203의 실제 notification adapter는 아직 develop에 없으므로 SQL 합성 adapter로 원자성을 검증했다.
실제 알림 상태 매핑·전달/취소 경합은 #203 연동에서 검증해야 한다. 따라서 이 PR만으로 #423 전체
인계나 #202·#203 통합 완료를 선언하지 않으며 이슈를 자동 종료하지 않는다.

의료 AI·Provider 호출·공개 gate 변경은 없으며 의료 eval은 적용 대상이 아니다.
Privacy Production 및 Track C/F gate는 유지한다.
