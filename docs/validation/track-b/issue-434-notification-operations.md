# #434 알림 정기 실행·복구 검증 및 Track B 인계

| 항목 | 값 |
| --- | --- |
| 검증일 | 2026-09-12 |
| 구현·조율 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰 | 송은영 (`phina-io`), Backend·DB·Security·운영 설정 |
| 기반 | develop `df259ab3`, #430 알림·#456 일정 API 포함 |
| 브랜치 | `codex/434-notification-operations` |
| Migration head | `e8c41a09d652`, 신규 migration 없음 |
| 환경 | macOS, Python 3.13, Docker PostgreSQL 17·Redis 7, 격리된 합성 test DB |
| 배포 여부 | Local Docker smoke만 실행. Staging/Production 미적용 |

## 변경과 발견한 운영 문제

기존 Backend 이미지를 사용하는 opt-in Compose 서비스를 추가했다. 실행 후 60초 대기,
45초 cooperative timeout, SIGTERM 취소·DB 정리, 다음 주기 재실행을 적용한다.
원래 생성·게시 분리 transaction·500 occurrence 한도·게시 자격 조건은 유지한다.
기존 로그 formatter는 `extra` 건수를 출력하지 않아 건수·UTC 완료 시각을 메시지 본문에 넣었다.
실패는 고정 reason과 시간만 기록하고 exception/SQL/식별자/의료 내용을 출력하지 않는다.

배포 Runtime allowlist에 `notification_record`가 빠져 있음을 확인했다. 해당 테이블만
SELECT·INSERT·UPDATE를 명시하고 DELETE·TRUNCATE는 허용하지 않는다. 기존 Admin
provisioning 절차 안에서 적용하며 API 컨테이너에 Admin 자격증명을 주지 않는다.
실제 PostgreSQL provisioning 통합 검증에 Runtime 생성·게시·재실행과 금지 권한 검사를 추가했다.
배포 스크립트는 migration 전에 알림 서비스를 중지하고 정지 확인 실패 시 중단한다.
시작은 승인 후 운영자가 명시적으로 수행한다.

공유 API·DTO·enum·schema·상태 의미·게시 조건·transaction 순서 변경은 없다.
기존 Proposed 알림 문서를 임의 승격하지 않는다. 그 상태 정합화와 Frontend 승인은 기존
담당 흐름에서 확인해야 하며, 본 운영 검증이 Track B 전체 또는 공개 승인 근거는 아니다.

## 시나리오와 증빙

| 확인 흐름 | 증빙 | 결과/해석 |
| --- | --- | --- |
| 예정 전 생성→예정 시각 게시→반복 무변경 | `test_repeated_command_generates_before_due_and_publishes_only_when_due` | 새 테스트; 명령의 실제 transaction 경계 |
| 동시 실행 중복 없음 | `test_concurrent_one_shot_commands_publish_only_once` | 기존 PostgreSQL 별도 session 테스트 재사용; 생성/게시 합계 1·attempt=1 |
| 생성 커밋 직후·게시 flush 후 실패 | `test_command_restart_recovers_committed_generation_and_rolled_back_publication` | 두 장애 지점에서 PENDING·attempt=0 보존 후 한 번만 게시 |
| timeout rollback·재실행 | `test_timeout_after_generation_rolls_back_publication_and_next_run_recovers` | 게시 후 대기 중 취소; 다음 명령 복구 |
| 500 초과 backlog→HTTP 목록 | `test_more_than_500_targets_drain_across_invocations_and_appear_in_http_list` | 합성 501 occurrence, 생성/게시 500→1→0, 목록 501건·ID 중복 0 |
| Check-in 쓰기 잠금과 게시 경합 | `test_checkin_lock_defers_publication_then_closed_occurrence_is_cancelled` | 기존 잠금 skip·후속 취소 검증 재사용 |
| 취소·완료·기한 경과 | `test_notifications.py`의 상태·deadline·real_checkin·deadline_scheduler 테스트 | 기존 실제 서비스 검증 재사용; 미전달 취소·전달 이력 보존 |
| 일정 PUT/PATCH→미전달 취소 | `test_medication_schedule_api.py::test_real_adapter_cancels_only_undelivered` | 기존 실제 API/adapter 검증 재사용 |
| 일정/알림/Audit rollback | `test_failure_rolls_back_every_table`, `test_same_session_notification_write_rolls_back_with_snapshot_cap` | 기존 교차 transaction 검증 재사용 |
| 주기 실패 후 대기·재실행/중지 | `test_notification_runtime.py` | 60초 순서·실패 고정 로그·종료 코드·SIGTERM 정리 검증 |
| 제한 DB 권한 | `test_database_role_provisioning.py` | 실제 migration/provisioning 뒤 생성·게시 성공, Runtime DELETE/TRUNCATE 및 Writer 조회/삽입 42501 |
| 운영 구성 | `test_notification_runtime_configuration.py` | opt-in·동일 이미지·Runtime 계정·Provider/volume 부재·migration 전 정지 |

Fixture는 기존 `notification-v1` 및 `_create_user_with_self_profile`·`_create_occurrence`의
SYNTHETIC 데이터다. 부하 테스트는 501개 별도 확정 일정 occurrence를 만들며 실제 시간의
성능 SLA·계속 유입되는 무한 backlog 처리량을 보증하지 않는다. 시간 조건은 고정 `NOW`로
검증한다. Docker smoke만 실제 UTC clock과 60초 주기를 사용한다.

## Local Docker 운영 검증

저장소 Backend Dockerfile로 `ah434-app:local`을 빌드했다. 운영 Compose 서비스 정의를
복사한 별도 `ah434-smoke` project에서 이미지·DB 접속값만 합성 local 값으로 바꾸고
PostgreSQL tmpfs 및 실제 Alembic upgrade head를 사용했다. 기존 개발/운영 DB와 분리했다.

- 10:36:10 UTC: 최초 성공, created=1·delivered=1·cancelled=0, 0.236초.
- 10:37:10 UTC: 다음 주기 성공, 0·0·0, 0.072초. 단순 중복 실행으로 재게시하지 않음.
- SIGTERM stop, 단발 명령 exit 0, force-recreate 확인.
- 제한 계정 `smoke_runtime`의 notification SELECT가 기본 거부됨을 확인한 뒤 실제
  provisioning 함수로 SELECT·INSERT·UPDATE만 부여; DELETE·TRUNCATE 거부 확인.
- 10:39:47 UTC: 제한 계정 성공, created=1·delivered=1·cancelled=0, 0.115초.
- 10:39:53 UTC: read-only 집계 SQL의 due pending·ineligible pending·미생성 대상 모두 0.
- 10:40:27 UTC: Local DB port=1 연결 실패 주입. reason=batch_error 고정 로그, exit 1.
- 10:40:28 UTC: 제한 계정 서비스 SIGTERM stop 로그 확인.
- 10:40:34 UTC: 제한 계정 단발 복구 exit 0, 0·0·0. 이후 force-recreate 성공.

최초 이미지 smoke는 local Admin 계정으로 연결/실행을 확인했고, 뒤의 성공과 권한 검증은
별도 제한 계정으로 수행했다. Production의 승인된 계정·이미지 digest·관제 수신자·외부
공개 게이트 검증을 대신하지 않는다.

## 재현과 필수 검사

[운영 Runbook](../../operations/notification-scheduler.md)의 시작·중지·재실행·집계 SQL을 사용한다.
로컬 DB 검증은 `envs/example.local.env`에서 만든 합성 환경파일과 전용 PostgreSQL 17·Redis 7
Compose를 `ENV_FILE`·`COMPOSE_FILE`로 지정했다. 환경파일/비밀값은 커밋하지 않는다.
`run_test.sh`·통합 runner는 같은 DB를 재생성하므로 동시에 실행하지 않는다.

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
ENV_FILE=/path/to/synthetic-local.env COMPOSE_FILE=/path/to/isolated-compose.yml bash scripts/ci/run_test.sh
bash -n scripts/deployment.sh
git diff --check
```

첫 관련 검사: 39 passed (23.10초). 이후 예정 시각 반복·SIGTERM·권한·배포 정지 검증을 보강했다.
최종 필수 검사·Commit SHA·PR/CI 링크는 아래 완료 기록에 남긴다.

## Track B Backend·Frontend 인계

| 범위 | 준비된 자료 | 남은 일 |
| --- | --- | --- |
| 일정 조회/PUT/PATCH | [#202 검증](./issue-202-schedule-api.md), [기존 합성 fixture](./issue-202-schedule-fixtures.json) | 한솔님 화면에서 실제 소비 확인 |
| 알림 목록·읽음·재알림 | [#203 증빙](./issue-203-notifications.md), `backend/app/tests/notifications/test_notifications.py` | #421 UI·기록 연결 E2E |
| Check-in 기록·수정 | [#202 Check-in 증빙](./issue-202-checkin-api.md), 실제 API 테스트 | #138 화면/실제 소비 |
| UNCONFIRMED 보완 | [PR #462](https://github.com/AI-HealthCare-05/AH_05_04/pull/462)의 fixture·DTO·cursor 검증 | 두 리뷰어 승인→계약 상태 정렬→최종 승인/CI→병합 |
| 정기 알림 | 본 기록·[Runbook](../../operations/notification-scheduler.md) | #434 리뷰 승인, #230의 환경별 실행 권한·배포·관제 연결 |

한솔님은 알림의 `occurrence_local_date`로 기존 날짜별 occurrence API를 조회하고
`occurrence_id`를 연결한다. 재알림의 `scheduled_at`으로 원래 복약 날짜를 추정하지 않는다.
`DELIVERED`/read_at은 Check-in 결과가 아니다. 목록은 offset=0부터 새로고침하며
401 인증, 소유권/미존재 404, 멱등키/현재 revision 관련 409, 요청 검증 422는 기존 API
테스트와 fixture의 code별 흐름을 따른다. 발생 가능한 code 전체는 해당 DTO/라우터 및
계약 문서를 참조한다. #462 자료는 병합 전이므로 develop에서 등록된 API로 가정하지 않는다.
Frontend 구현이나 새로운 fixture 계약을 본 PR에서 추가하지 않았다.

#462는 작업 중 재조회 시 리뷰 0건, `phina-io`·`solia142` 검토 요청 상태였다.
기존 승인을 최종 HEAD 승인으로 간주하지 않는다. #422는 이번 변경 범위에 포함하지 않았다.
