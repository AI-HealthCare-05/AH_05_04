# #670 명시적 시간 후보 — Local 검증

검증일: 2026-09-17. 대상: PR #677 구현 커밋 `9ac81b1b` (이후 검증 문서 갱신은 실행 코드 변경 없음).
상태: Local 합성 검증 / 담당·전문 리뷰 대기. 실제 환자·의료문서·외부 Provider를 사용하지 않는다.
[Decision](../../governance/decisions/2026-09-17-explicit-schedule-recommendation-670.md),
[Proposed 계약](../../contracts/proposed/track-b-explicit-schedule-recommendation-v1.md)을 함께 검토한다.

## 범위와 결과

- 확정 문구의 명시적 식후 간격만 계산한다. `저녁 식후 30분`과 종료 `19:30` → `20:00`.
- `저녁 식후`, 범위·필요시·격일·추가 지시·누락·횟수 불일치·중복·자정 넘김은 후보 없음.
- 후보 조회는 DB를 변경하지 않는다. 타인 ID·존재하지 않는 ID는 동일 404, 과거 처방은 409.
- 수정된 시각·기준 입력·parser version 불일치는 저장 전 거부한다.
- 성공 요청 replay는 중복 생성하지 않으며 기존 일정은 추천 경로로 덮어쓰지 않는다.
- context 없음/null의 기존 수동 저장 fingerprint가 동일한지 검증한다.
- 사용자 입력 변경 시 후보 무효화, 늦은 응답 무시, 명시적 적용과 저장 분리,
  저장 실패 입력·멱등 키 유지, 직접 입력 전환을 검증한다.
- Backend Local gate와 DEV UI로 비공개 경계를 유지한다. 약학적 적절성·일반적 약별 제약 검증은 아니다.

## 자동 검사

| 검사 | 결과 |
| --- | --- |
| `tests/contract/test_schedule_recommendations.py` | 33 passed |
| PostgreSQL 17 `backend/app/tests/medication_schedules/test_medication_schedule_api.py` | 40 passed |
| Frontend ScheduleRecommendation / SchedulePage / MedicationSchedulesApi | 62 passed |
| Frontend 전체 Vitest | 743 passed / 44 files |
| Playwright `e2e/schedule-recommendation.spec.ts` | 320px·390px 2 passed; 가로 넘침 없음, 적용·저장 payload 확인 |
| Ruff check / format check | 통과 |
| Mypy Backend·Worker | 통과 |
| Frontend lint / production build | 통과 (기존 500kB chunk 안내 있음) |
| Python test inventory | 통과 |
| `git diff --check` | 통과 |
| 전체 runner migration | 241 passed / 4 skipped |
| 전체 runner Backend·계약·선별 통합 | 2,923 passed / 128 skipped |
| 전체 runner Redis 통합 | 29 passed |
| Worker 전체 lane 재검증 | 4,154 passed |
| Backend + 재실행 Worker coverage 합산 | 92%, coverage report 종료 코드 0 |

초기 전체 Frontend 실행에서 변경하지 않은 OCR STALE 테스트가 1회 실패했으나, 단독 재현과
전체 재실행에서 통과했다. 첫 실행 결과를 숨기지 않으며 이번 변경의 실패로 판정하지 않는다.
초기 Worker lane은 테스트용 REDIS_HOST 값을 승인 기본값과 다르게 지정해 1개가 실패했다.
환경값을 맞춘 단독 검사와 전체 Worker 재실행에서 각각 통과했다. 구현 코드를 바꾸지는 않았다.
원본 `run_test.sh` 종료 코드는 이 초기 Worker 실패 때문에 1이다. Backend lane은 통과했으며,
완료된 Backend coverage와 성공한 Worker 재실행 coverage를 별도로 combine·report해 92%를 확인했다.
건너뛴 migration 4개·Backend lane 128개는 이번 신규 테스트가 아니며, 전체 통과로 표현하지 않는다.
브라우저 검증은 합성 API mock이고, 실제 DB API 검증과 구분한다.
의료 AI 모델·RAG·Provider를 변경하지 않아 기존 생성/검색 eval runner는 실행 대상이 아니다.
안전 관련 거부 사례는 위 계약·API 테스트에서 결정적 산술 계산에 대해 검증한다.

## 재현

Frontend: `VITE_API_BASE_URL=http://localhost:8000 npm run test`, `npm run lint`, `npm run build`.
Playwright: Frontend에서 `pnpm exec playwright test e2e/schedule-recommendation.spec.ts`.
Python: 합성 Local DB 설정과 `PYTHONPATH=backend:.`를 지정하고 위 pytest 경로를 실행한다.
API 테스트는 기존 공통 fixture가 literal `test` DB를 초기화하므로 개발 DB와 분리된 PostgreSQL 17을 사용한다.
전체 runner 역시 이번 작업 전용 Compose·test DB·Redis를 사용한다.

## 검토에 필요한 판단

송은영: API·재계산·멱등 호환·transaction·Local gate. 정현우: 완전 일치 문법만 사용하는 제한과
근거 부족 거부. 남한솔: 매일 동일 식사 확인·직접 입력 전환·후보 수정 흐름.
이 문서는 해당 담당자의 승인 증빙이 아니며 #670 전체 완료 또는 Production 개방을 선언하지 않는다.

## 2026-09-17 리뷰 반영 검증 — 후보 계산 잠금

- 책임 리뷰어의 [배타 잠금 지적](https://github.com/AI-HealthCare-05/AH_05_04/pull/677#issuecomment-5707643616)을 반영했다.
- 수정 전 독립 PostgreSQL 세션의 `SELECT FOR UPDATE NOWAIT`가 `LockNotAvailableError`로 실패함을 재현했다.
- 수정 후 후보 조회 transaction을 연 채 다른 세션이 즉시 쓰기 잠금을 얻으며, 반대로 쓰기 잠금이 있는
  동안에도 후보 조회가 완료되는 것을 검증했다. 실제 저장의 잠금은 기존 경로에 유지한다.
- `is_owned()`는 활성 version을 검사하지 않으므로 그대로 대체하지 않았다. 잠금 없는 조회에
  활성 version 조건과 snapshot fingerprint 검증을 유지해 과거 처방 및 무결성 위반을 거부한다.
- 일정 API·Repository·mutation·추천 계약 회귀 **106 passed**. Ruff check/format, Mypy, diff check 통과.
- 이 리뷰 수정에서는 Frontend·Worker·migration을 변경하지 않았다. 위 전체 실행 결과는 이전 구현
  커밋의 기록이고, 이번에는 영향 범위의 PostgreSQL 회귀와 정적 검사를 실행했다. 최신 필수 CI는 PR에서 확인한다.
