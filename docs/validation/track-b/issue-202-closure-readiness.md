# #202 종료 준비 감사

| 항목 | 값 |
| --- | --- |
| 기준일 | 2026-09-13 |
| 기준 develop | f56a6322 (#467 병합 포함) |
| 별도 열람 | #468 head 0f068b72, OPEN |
| 결과 | **Backend 조회 구현·관련 100건 PASS — 원격 CI·책임 리뷰·Frontend 인수 대기** |
| 구현 코드 | `4231fb85`; 후속 검증 기록 커밋은 문서만 변경 |
| 변경 범위 | 조회 API·DTO·SELF query·실제 HTTP 테스트·합성 fixture·문서 |

## 종료 조건별 남은 증빙

| #202 조건 | 확인 근거 | 종료 전 조치 |
| --- | --- | --- |
| 일정·Check-in 정상/오류·OpenAPI·소유권·revision | [일정 API 검증](issue-202-schedule-api.md), [Check-in API 검증](issue-202-checkin-api.md), #413/#456 | 최종 develop에서 필수 검사와 관련 테스트 증빙 연결 |
| setup_reason 단일값·우선순위 | [PD-417](../../governance/decisions/2026-09-10-track-b-schedule-contract.md), #424/#438 | 과거 이슈 댓글의 4값/TBD 대신 승인된 5값 기준으로 완료 기록 |
| 알림→원래 날짜·읽음/Check-in 분리 | [#468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468) 테스트·fixture | 병합 SHA·최종 CI·인계 수락 증빙 확인 |
| 과거 약 상세 표시·fixture | [새 Proposed 계약](../../contracts/proposed/track-b-occurrence-medication-v1.md) | 이 브랜치의 구현·HTTP 검증·합성 fixture 제공 완료; 최종 책임 리뷰·소비 인수 필요 |
| 개인정보·접근성·의료 안전 영향 | 조회 최소 필드·SELF 404·no-store·읽기 불변성 | Backend 검증과 소비 검토 기록; 브라우저 UI 검증은 #421/#138 |
| reason_code 제외 | 기존 #202 계약·DTO·테스트 | 최종 DTO/OpenAPI 정합성 검사와 연결 |

## 이번 구현 검증

- Python 3.13, 별도 Compose `track-b-202-test`, PostgreSQL 17의 `test` DB·Redis. 실제 사용자·Provider 호출 없음.
- 신규 실제 HTTP/OpenAPI 8건을 포함한 알림·일정·Check-in 회귀: **100 passed / 31.38s**.
- Ruff check/format: **PASS (784 files)**. Mypy: **PASS (593 source files)**.
- 필수 전체 runner: migration **210 passed / 4 skipped**, head `e8c41a09d652` 확인.
  Backend **1,954 passed / 85 skipped / 391.97s**, Redis 통합 **24 passed / 6.82s**.
- Worker 첫 실행 **3,076 passed / 8 skipped / 1 failed**: 격리 환경파일의 Redis host override로
  기본 설정 검사가 실패했다. override 제거 후 영향받은 `ai_worker/tests/core/test_config.py`는
  **76 passed / 0.30s**. 런타임 코드는 수정하지 않았다. 최초 전체 runner는 exit 1이며, 실패한 설정 파일 재검사만 통과했다.
  해당 실패로 전체 coverage combine/report는 실행되지 않았다. 단발 전체 PASS나 coverage 수치를 주장하지 않는다.
- [구현 PR #474](https://github.com/AI-HealthCare-05/AH_05_04/pull/474), 원격 CI 진행 중.
- `git diff --check`, 변경 범위·합성 fixture 관계·상대 링크 및 Pandoc HTML 구조 검토: PASS.
  Frontend 브라우저 E2E는 #421/#138 담당 범위로 이번에 실행하지 않았다.
- 최초 신규 검사 7 PASS/1 FAIL은 오류 응답 후 테스트 ORM 객체 접근으로 발생한 MissingGreenlet이었다.
  요청 path를 오류 전 보관하도록 테스트를 수정했고 위 100건 재실행에서 모두 통과했다.
- [합성 HTTP 응답](issue-202-occurrence-medication.json): 실제 ASGI 실행에서 추출.
  과거 날짜의 occurrence와 원래 약의 세 ID, 새 version 분리, 알림·Check-in 연결도 대조했다.
- DB schema·migration·권한·dependency 변경 없음. 기존 API 의미·쓰기 transaction 변경 없음.

재현 시 `scripts/ci/run_test.sh`의 기존 local/test 환경 설정을 사용한다. 새 조회 검사는
`pytest backend/app/tests/notifications/test_occurrence_medication_handoff.py -q`, 관련 회귀는
`pytest backend/app/tests/notifications backend/app/tests/medication_schedules backend/app/tests/medication_checkins -q`다.
검사는 비식별 합성 `test` DB에서 직렬 실행한다.

## SCHEDULE_REVISION_CONFLICT 문서 WATCH

현재 [Check-in target](../../contracts/targets/post-mvp-1/checkin-v1.md)의 목표 오류 목록과
[승인된 일정 정합화](../../contracts/targets/post-mvp-1/track-b-schedule-reconciliation-v1.md)는
stale expected revision을 `409 SCHEDULE_REVISION_CONFLICT`로 명시한다.
[일정 API Proposed](../../contracts/proposed/track-b-schedule-api-v1.md)와
[실제 API 테스트](../../../backend/app/tests/medication_schedules/test_medication_schedule_api.py)도 같은 code를 사용한다.
따라서 이번 과거 약 조회를 위해 이 code를 새로 만들거나 의미를 바꿀 필요는 없다.
PD-417 승인 증빙과 #456의 최종 코드·검증을 #202 완료 기록에 연결하고, 기존 WATCH가
가리킨 문구의 최종 해소를 검토한 뒤 체크한다. 정적 문자열 일치를 별도 승인으로 간주하지 않는다.

## 후속 이슈와 구분

- #202: Backend 제공·계약/통합 검증·Frontend fixture 인계 완료 여부를 판정한다.
- [#421](https://github.com/AI-HealthCare-05/AH_05_04/issues/421), [#138](https://github.com/AI-HealthCare-05/AH_05_04/issues/138): 남한솔의 화면 구현·모바일/접근성·실제 브라우저 E2E.
- [#434](https://github.com/AI-HealthCare-05/AH_05_04/issues/434): #467 정기 실행·복구 검증을 완료 조건과 대조. Production은 #230의 권한·승인·별도 배포 절차.
- #462: develop `36890a43`에 병합된 UNCONFIRMED backlog. 모든 과거 알림의 약 조회를 대신하지 않는다.
- IT-3 전체 통합과 Production 공개는 각각 별도 증빙을 유지한다. #202 종료로 자동 PASS 처리하지 않는다.

## 종료 기록에 남길 항목

1. 최종 병합 SHA·migration head·환경·합성 fixture version·CI/필수 검사 결과.
2. 새 조회 계약 Decision·담당 리뷰 결과·원래 약 연결 HTTP 검증·Frontend 인계 수락.
3. #202 체크리스트 항목별 근거와 남은 #421/#138/#434/IT-3 작업 링크.

위 증빙이 모두 확보된 뒤 #202를 종료한다. 현재 이슈 내용·상태는 수정하지 않았다.
