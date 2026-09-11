# #202 일정 API 연결 검증

| 항목 | 값 |
| --- | --- |
| 테스트 기준 develop | `8010dfce` (#430; #438 저장 기반 포함) |
| 최종 PR 기반 | `79934df3` (#452 문서 1개 변경만 추가; 코드·테스트·migration diff 없음 확인 후 rebase) |
| 작업 브랜치 | `codex/202-schedule-api` |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰 | 송은영 (`phina-io`), 남한솔 (`solia142`); 승인 대기 |
| 환경 | 2026-09-11, macOS, Python 3.13.9, 로컬 PostgreSQL·Redis, 격리 `test` DB |
| Migration Head | `203a1b2c3d4e`, 부모 `423a1b2c3d4e`; 이번 PR 신규 migration 없음 |
| Fixture | `issue-202-schedule-v1`, SYNTHETIC; 실제 환자 데이터·외부 AI 호출 없음 |

## 변경·검증 범위

실제 FastAPI 앱에 날짜별 GET·일정 PUT/PATCH를 연결한다. 기존 저장 서비스와
NotificationRepository는 요청 DB 의존성과 같은 AsyncSession을 사용한다.
Check-in PUT, Scheduler, 알림 목록·읽음 구현을 중복 작성하지 않는다.

[API 테스트](../../../backend/app/tests/medication_schedules/test_medication_schedule_api.py)는
아래 경계를 실제 PostgreSQL·ASGI 앱으로 검증한다.

- 최초 생성, 같은 payload의 새 키 수정, 취소·반복취소·재활성화, ENDED 보존.
- 같은 키 최초 응답 재현, 순서만 다른 시각의 같은 지문, 다른 지문 및 stale revision 409.
- 빈/중복/형식 오류 시각, frequency mismatch, 날짜 조합, bool/string revision, unknown field 미저장.
- 실제 미전달 알림 취소와 전달됐지만 미읽음인 알림 보존; PUT/PATCH 각각 검증.
- 실제 adapter가 취소를 저장한 직후 실패, audit 저장 직후 실패, occurrence 생성 직후 실패,
  암호화 snapshot cap 초과에서 schedule·time·occurrence·audit·idempotency row 수와
  revision/status·알림 취소 상태 전체 rollback.
- SETUP_REQUIRED/READY/PARTIAL/INACTIVE/NO_ACTIVE_PRESCRIPTION, 현재 Check-in 응답 재사용,
  과거 pending과 occurrence 원래 KST 날짜 보존, 23시 일정의 +4시간 deadline.
- SELF 교차 사용자 GET 격리와 PUT/PATCH 동일 404, 인증·날짜 validation, no-store·trace.
- 과거 version 동일 키 재현, 새 키 PUT/PATCH version conflict, 새 version 재설정과 과거 occurrence 보존.
- 실제 OpenAPI의 method·필수 header·DTO requiredness·공통 오류와 Frontend fixture DTO 검증.

현재 snapshot에 정확한 시작일/시각/pattern 저장 필드가 없으므로 미설정 reason은
MISSING_START_DATE다. 나머지 승인 reason 분기를 실행했다거나 Frontend 검토를 받았다고
주장하지 않는다. 기존 #438의 종료·PUT·rolling·활성화 잠금 경합 테스트와 #430의 알림
생성·전달 경합 테스트는 필수 전체 runner에 포함되며 API 신규 경합 테스트로 계산하지 않는다.

## 재현 명령

저장소 기여 지침의 필수 명령:

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
bash scripts/ci/run_test.sh
```

이 작업은 별도 worktree에서 원래 local 환경파일과 실행 중인 Compose 프로젝트를
ENV_FILE·COMPOSE_FILE·COMPOSE_PROJECT_NAME으로 지정했다. runner는 literal test DB만
재생성한다. 공유 test DB를 사용하는 runner와 개별 pytest를 동시에 실행하지 않는다.
신규 테스트는 같은 격리 DB 설정으로 `pytest backend/app/tests/medication_schedules -q`를 실행한다.

## 결과

- 신규 일정 API·DB·OpenAPI·fixture: **29 passed** (4.56s).
- Ruff check / format check: **PASS** (756 files).
- Mypy: **PASS** (579 source files).
- 최초 전체 실행: migration 202 PASS / 3 SKIP, Worker 2,974 PASS / 8 SKIP. Backend는 신규 fixture 오류
  4건으로 실패했다(짧은 멱등 키 2건, fingerprint 갱신 누락, 파일 상대 경로). 기존 Backend
  1,920건은 통과했고 신규 fixture 수정 후 위 29건 재실행이 통과했다.
- 최종 `bash scripts/ci/run_test.sh`: **PASS (exit 0)**.
  - Migration: **202 passed / 3 skipped**, 157.43s; 실제 head 검증 PASS.
  - Backend·계약·선별 PostgreSQL: **1,925 passed / 65 skipped**, 332.10s.
  - Redis 통합: **23 passed**, 5.29s.
  - Worker: **2,974 passed / 8 skipped**, 101.78s.
  - 종합 coverage: **93%**. skip은 기존 환경/옵트인 조건이며 실행된 PASS로 계산하지 않는다.
- Markdown 렌더 구조·신규 상대 링크, 전체 diff 범위 및 `git diff --check`: **PASS**.

테스트 시나리오와 실행 결과는 지정 리뷰어 승인을 대신하지 않는다.

## 후속 경계

HTTP 구체화와 [합성 fixture](./issue-202-schedule-fixtures.json)는 지정 리뷰어의 검토 대상이다.
[계약](../../contracts/proposed/track-b-schedule-api-v1.md)과
[Decision](../../governance/decisions/2026-09-11-schedule-api-202.md)을 Current로 승격하지 않는다.
#418 라우터 등록·승인 정리, #138/#421 Frontend·E2E, #434 정기 실행은 별도 작업이다.
Check-in history의 화면 범위 승인도 이번 연결로 완료됐다고 해석하지 않는다.
