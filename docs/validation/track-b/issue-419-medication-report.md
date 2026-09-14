# #419 공통 복약 집계 검증

| 항목 | 값 |
| --- | --- |
| 상태 | 작업 브랜치 구현·로컬 검증, 지정 리뷰·병합 대기 |
| 기준 | develop `f10ca016`에 rebase, `feat/419-medication-report` |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰 | 송은영 (`phina-io`): Backend/API·DB·Security; 남한솔 (`solia142`): Frontend 소비 계약 |
| 계약 | [공통 복약 리포트 v1](../../contracts/proposed/medication-report-v1.md) |
| Decision | [PD-419-20260913](../../governance/decisions/2026-09-13-medication-report-419.md) |

## 구현

- `GET /api/v1/medication-reports`: SELF의 원래 KST 예정일 기준 7/30일 조회.
- Repository 한 SELECT의 동일 행 집합에서 상세 records와 두 비율을 계산한다.
- 과거 version 및 취소/종료 일정의 보존 기록을 포함하고 최신 Check-in만 집계한다.
- TAKEN/NOT_TAKEN/UNCONFIRMED를 구별한다. PENDING/CANCELLED는 비율에서 제외한다.
- 분모 0은 null, 백분율은 Decimal ROUND_HALF_UP으로 소수 첫째 자리까지 반환한다.
- deadline만으로 조회 중 Check-in을 생성하지 않고 처리 지연 PENDING 수를 표시한다.
- 기존 Schedule/Check-in 쓰기, Push, DB schema, 공개 gate를 변경하지 않는다.

## 검증 결과

- 집계 전용 API·DB·계약 테스트: **17 passed** (2.21초).
- `uv run ruff check .`: 통과.
- `uv run ruff format . --check`: 789 files, 통과.
- `uv run mypy backend/app ai_worker`: 598 source files, 통과.
- `bash scripts/ci/run_test.sh`: **exit 0**.
  - migration: 210 passed, 4 skipped (156.92초).
  - Backend·계약·선별 PostgreSQL: 1,966 passed, 85 skipped (376.78초).
  - Redis 통합: 24 passed (5.30초).
  - Worker: 3,077 passed, 8 skipped (145.16초).
  - 총 5,277 passed, 97 skipped. 기존 opt-in/환경별 skip을 통과로 합산하지 않았다.
  - 결합 coverage 92%, DB head `e8c41a09d652` 검증 통과.
- 전체 diff·상대 링크·`git diff --check`: 통과.
- AI 출력이나 의료 해석 변경이 없어 추가 의료 AI eval은 적용하지 않았다.

검증은 2026-09-13 macOS Python 3.13.9에서 기존 개발 venv를 재사용했다.
`UV_PROJECT_ENVIRONMENT`를 기존 venv로 지정하고 `UV_NO_SYNC=1`로 의존성 변경 없이 실행했다.
환경은 저장소 `envs/example.local.env`의 공개 placeholder만 사용했다.
운영 비밀정보, 실제 환자 데이터, 외부 Provider 호출을 사용하지 않았다.

#469와 DB를 공유하지 않도록 `/private/tmp/issue419-compose.yml`의
`issue419-tests` Compose project에 PostgreSQL 17 (55419)과 Redis 7 (56419)을 띄웠다.
PostgreSQL 데이터는 tmpfs이며 runner는 이 인스턴스의 `test` DB만 재생성한다.
기존 개발/운영 DB·다른 작업의 test DB를 변경하지 않았다.
검증 후 전용 컨테이너·network를 종료·제거했다.

필수 runner 재현 명령(해당 임시 Compose와 예제 env가 준비된 로컬):

```bash
ENV_FILE=/private/tmp/issue419-test.env \
COMPOSE_FILE=/private/tmp/issue419-compose.yml \
bash scripts/ci/run_test.sh
```

## PR 생성 전 최신 develop 반영

2026-09-13 #477이 병합된 develop `f10ca016`으로 충돌 없이 rebase했다.
위 전체 필수 runner 결과는 기존 기준 `0ea12641`에서 실행한 증빙이다.
최신 기준에서는 #419 집계 API·DB·계약 테스트 17개를 다시 실행해 통과했다(2.34초).
Ruff check·format check(794 files)와 Mypy(601 source files)도 재통과했다.
전체 필수 runner는 rebase 후 중복 실행하지 않았으며 PR CI에서 최신 head를 검증한다.

## 자동 검증 범위

`backend/app/tests/medication_reports/test_medication_report_api.py`가 다음을 확인한다.

- 7일·30일 양 끝 포함, 범위 밖 제외, UTC 전날에 해당하는 KST 시작일 기록 보존
- 2 TAKEN / 1 NOT_TAKEN / 1 UNCONFIRMED → 복용률 66.7%, 기록 확인률 75.0%
- 빈 집합, UNCONFIRMED만, PENDING/CANCELLED만 및 반올림 tie 6.25% → 6.3%
- deadline 직전·동일·이후 조회가 쓰기를 하지 않음
- 실제 Scheduler 후 Check-in HTTP 정정(UNCONFIRMED→TAKEN→NOT_TAKEN), 원래 날짜·revision·갱신 시각 반영
- 새 version 확정 및 schedule 취소 후에도 과거 결과 보존
- 타 사용자 데이터 제외, SELF 없는 조회와 인증 오류
- KST 오늘 기본값, 잘못된 query·미래 종료일·date underflow
- OpenAPI operation ID, period enum, 공통 오류 및 응답 DTO

## Frontend 합성 fixture

[issue-419-report-fixtures.json](./issue-419-report-fixtures.json)은 `empty`, `mixed`,
`only_unconfirmed`, `pending_and_cancelled`, `corrected` 사례를 담는다.
기본 리포트와 진료 보기에 같은 fixture를 사용한다. null 비율을 0%로 바꾸거나
UNCONFIRMED를 NOT_TAKEN에 합산하지 않는다. 이 fixture는 DB seed나 실제 환자 데이터가 아니다.

## 남은 승인

사용자의 구현 진행 확인을 지정 리뷰어 승인으로 대체하지 않는다.
송은영·남한솔 리뷰 및 병합·Current 승격은 남아 있다. Track C/F 및 공통 Privacy Production gate는 유지한다.
