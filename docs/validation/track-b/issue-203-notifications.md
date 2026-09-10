# #203 앱 내부 알림 구현·검증 기록

| 항목 | 값 |
| --- | --- |
| 기준일 | 2026-09-10 |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 기술 리뷰 | 송은영 (`phina-io`) |
| 소비 계약 리뷰 | 남한솔 (`solia142`) |
| 상태 | 구현 브랜치 검증 · 지정 리뷰어 승인 및 #202 일정 API/Frontend 통합 대기 |
| 계약 | [PD-203 Notification](../../contracts/proposed/track-b-notifications-v1.md), 선행 문서 PR #415 |
| 기반 | develop `2922c25d` |
| Migration head | `203a1b2c3d4e` (base `206a1b2c3d4e`), 단일 head |
| Fixture | `notification-v1`: 코드에 고정된 합성 사용자·처방·KST 자정 사례 |
| 환경 | Local, 작업 전용 PostgreSQL 17·Redis 7, 실제 사용자/외부 Provider 호출 없음 |

## 구현 범위

- Notification 모델·migration, SELF parent chain Repository, 목록·읽음·재알림 API와 OpenAPI DTO.
- 최초 알림·재알림 각각 occurrence당 하나, 암호화 동기 snapshot, 다른 사용자 404, no-store.
- 생성·게시 one-shot 명령, 기한/CLOSED/CANCELLED 재검증, 최초 read_at 유지.
- 기존 `PrescriptionVersionMedicationInvalidationService`에 동일 session 취소 adapter 주입. 실패는 상위 transaction에 전파한다.
- empty downgrade/upgrade, 이력이 있는 downgrade 차단과 실제 PostgreSQL 제약 검증.

## 실행 결과

- PASS: `ruff check .`, `ruff format . --check`, `mypy backend/app ai_worker`.
- PASS: 알림 전용 27개 테스트. API·DB·동시성·명령 실행·Check-in/UNCONFIRMED 연결 검증.
- PASS: 초기 알림·기존 Check-in·occurrence service 선별 회귀 43개.
- PASS: 필수 CI의 migration 테스트 149개. 새 알림 migration의 제약·중복 차단·이력 보존 downgrade guard 포함.
- 전체 CI Backend·Worker·Redis·Coverage 결과: 실행 중, 완료 후 이 행을 갱신한다.

검증 명령:

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
# Local 합성 테스트 전용 env/compose를 지정한다.
ENV_FILE=/path/to/test.env COMPOSE_FILE=/path/to/test-compose.yml bash scripts/ci/run_test.sh
```

`backend/app/tests/notifications/`의 concurrency fixture는 별도 schema에서 독립 session을 사용한다. 동일 key의 동시 재알림은 최초 response replay, 다른 key 경합은 하나의 알림과 중복 오류로 수렴한다. Check-in이 occurrence를 잠그는 동안 게시를 건너뛰고, CLOSED commit 뒤 다음 배치에서 취소되는 것을 검증한다. 두 one-shot 명령 동시 실행도 게시 1회로 수렴한다.

## 배치 실행과 배포 연결

```bash
PYTHONPATH=backend:. uv run --env-file /path/to/local.env python -m app.commands.process_notifications
```

생성 최대 500 occurrence와 게시/취소 최대 500 occurrence를 별도 transaction으로 처리한다. 한 실행 뒤 잔여 대상은 다음 호출이 처리한다. 기록이 commit된 뒤 프로세스가 중단되어도 다음 호출이 PENDING을 다시 조회하므로 외부 보상 큐는 필요하지 않다. 정상 로그는 created/delivered/cancelled 집계만 포함한다.

실제 정기 실행 주기·배포 scheduler 등록은 이번 작업에서 수행하지 않았다. 배포 담당자가 부하·알림 지연 요구에 맞춰 one-shot 명령을 정기 호출하도록 연결해야 한다. Backend 배포 전 migration을 적용하고, 배치 중단·재시작에 따른 지연을 확인한다. 외부 Push/SMS/Email 발송 설정은 없다.

Rollback 시 먼저 배치와 신규 쓰기를 중단하고 이전 애플리케이션으로 되돌리되 테이블·이력은 보존한다. 빈 테이블에서만 base `206a1b2c3d4e`로 downgrade가 가능하다. 알림 이력이 있으면 guard가 중단하며 임의 삭제 후 재시도하지 않는다.

## 남은 통합·완료 조건

- #415 최신 계약에 대한 기술 리뷰와 구현 PR의 지정 리뷰어 승인. 문서 승인과 구현 승인을 구분한다.
- #202 Schedule PUT/PATCH가 제공되면 일정 변경의 동일 transaction에서 이 취소 adapter를 주입한다. 현재 기반에는 Check-in PUT만 존재한다.
- #202 Occurrence GET과 약 표시 DTO 연결, 과거 version·원본 날짜 조회 및 Frontend fixture/E2E. 현재 API가 있다고 가정한 mock 통과로 대체하지 않는다.
- Track C REMINDER_SETUP은 기존 일정 PUT 흐름을 소비한다. occurrence 재알림 POST 사용을 추가하지 않는다.
- 위 통합·운영 실행 증빙 전에는 #203 전체 완료, Track B 완료 또는 Production 공개를 선언하지 않는다.
