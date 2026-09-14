# 공통 복약 리포트 계약 v1 — #419 제안

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed · 사용자 구현 기준 확인, 작업 브랜치 구현·검증, 지정 리뷰 대기 |
| Decision | [PD-419-20260913](../../governance/decisions/2026-09-13-medication-report-419.md) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰 | 송은영 (`phina-io`): Backend/API·DB·Security; 남한솔 (`solia142`): Frontend 소비 계약 |
| 추적 | [Issue #419](https://github.com/AI-HealthCare-05/AH_05_04/issues/419) |
| 조사 기준 | develop `0ea12641`, 2026-09-13 GitHub 이슈 본문(댓글 0건) |

2026-09-13 사용자가 이 계약안 기준의 구현 진행을 확인했다. 아래 기준으로 작업 브랜치에
구현했으며, 지정 리뷰어의 승인이나 병합된 runtime 계약을 의미하지 않는다.
이슈에서 확정된 두 계산식과 기존 Check-in 상태 의미는 유지한다.

## HTTP 제안

`GET /api/v1/medication-reports?period_days=7&end_date=2026-09-13`

OpenAPI operationId: `medication-reports.get`.

- `period_days`: 필수, 7 또는 30.
- `end_date`: 선택, ISO 날짜. 생략하면 요청 시작 시각의 Asia/Seoul 날짜.
- `start_date = end_date - (period_days - 1)일`. 양 끝 날짜를 포함한다.
- 미래 end_date와 start_date 계산이 불가능한 날짜, 잘못된 query는 기존 `422 VALIDATION_FAILED`.
- 인증은 기존 인증 의존성을 사용한다. `200`, `data` envelope, 공통 오류 및 no-store 적용.
- 사용자·profile 식별자를 query로 받지 않는다. 인증 사용자 SELF 소유 데이터만 조회한다.
- 빈 집합도 `200`. 신규 오류 코드·DB·migration·환경변수·공개 flag는 필요하지 않다는 설계안이다.
- 기본 리포트와 진료 보기는 같은 API 결과를 소비한다. 별도 mode나 저장 모델은 만들지 않는다.

## 기간과 처방 범위 제안

- 기준은 occurrence에 저장된 `scheduled_local_date`다. Check-in 작성일·정정일·taken_at으로
  기록을 다른 날짜로 이동하지 않는다. UTC 저장 시각은 KST 원래 예정일에 대응한다.
- 기존 날짜별 조회와 같은 occurrence → schedule → version medication → version →
  prescription → SELF 소유권 chain을 사용한다.
- 기간에 속한 소유 occurrence의 현재·과거 prescription version을 모두 포함한다.
  active_version이나 현재 schedule 상태로 과거 기록을 제거하지 않는다.
- 여러 처방에 남아 있는 기존 기록은 원래 식별자로 구별한다. 처방을 새로 병합하거나
  중복 약 판단·다중 처방 관리 정책을 구현하지 않는다.
- 저장된 occurrence만 조회한다. 일정 미설정·미생성 날짜를 미복용이나 미확인으로 채우지 않는다.
- 오늘을 포함하면 오늘의 아직 응답하지 않은 occurrence도 PENDING으로 표시한다.
- 미래 end_date는 허용하지 않지만 오늘 날짜의 아직 도래하지 않은 시간은 표시한다.

## 상태와 두 지표

| 분류 | 계산/표시 |
| --- | --- |
| TAKEN | 현재 Check-in이 TAKEN인 횟수 |
| NOT_TAKEN | 현재 Check-in이 NOT_TAKEN인 횟수 |
| UNCONFIRMED | 현재 Check-in이 UNCONFIRMED인 횟수 |
| PENDING | 결과가 없고 occurrence가 PENDING인 횟수. 두 비율에서 제외 |
| CANCELLED | 취소 occurrence 횟수. 두 비율에서 제외 |

- 복용률 `adherence_rate`: TAKEN / (TAKEN + NOT_TAKEN).
- 기록 확인률 `confirmation_rate`: (TAKEN + NOT_TAKEN) / (TAKEN + NOT_TAKEN + UNCONFIRMED).
- 각 지표는 `numerator`, `denominator`, `percentage`를 반환한다.
- `percentage`는 0~100 숫자, 소수 첫째 자리 ROUND_HALF_UP. 분모 0이면 null.
  분자·분모는 반올림하지 않는다. UI는 null을 0%로 바꾸지 않는다.
- 빈 집합은 모든 count=0, percentage=null, records=[].
- CLOSED는 occurrence lifecycle 값이며 네 번째 Check-in 결과가 아니다.
- deadline 경과만으로 조회 중 UNCONFIRMED를 만들거나 상태를 바꾸지 않는다.
  기존 Scheduler가 저장한 현재 결과만 집계한다. 지연된 처리가 숨겨지지 않도록
  `overdue_pending_count`에 결과 없는 PENDING 중 `confirmation_deadline_at <= as_of`인 수를 반환한다.
  이 값은 pending_count의 부분집합이며 두 비율의 분모에 넣지 않는다.
- API 결과는 조회 시점의 현재값이다. end_date 시점의 과거 상태 재현 기능이 아니다.
  기간 밖에서 정정해도 원래 예정일의 현재 결과로 반영한다.

## 응답 DTO 제안

모든 아래 필드는 명시적으로 반환하고 nullable 필드만 null을 허용한다.

| data 필드 | 타입·의미 |
| --- | --- |
| period_days | 7 또는 30 |
| start_date, end_date | ISO 날짜 |
| timezone | 고정 문자열 Asia/Seoul |
| as_of | 요청 시작 UTC aware datetime. 과거 DB snapshot 시각을 보장하는 필드가 아님 |
| counts | taken_count, not_taken_count, unconfirmed_count, pending_count, cancelled_count: 0 이상 정수 |
| overdue_pending_count | 위 deadline 지연 수 |
| adherence_rate, confirmation_rate | numerator, denominator: 0 이상 정수; percentage: nullable 숫자 |
| records | 기간 내 occurrence 목록. scheduled_at ASC, occurrence_id ASC |

각 record는 기존 날짜별 occurrence DTO와 같은 `occurrence_id`,
`prescription_version_id`, `prescription_version_medication_id`, `scheduled_local_date`,
`scheduled_at`, `confirmation_deadline_at`, `status`, nullable `checkin`을 사용한다.
record의 `updated_at`은 occurrence와 현재 Check-in의 updated_at 중 최신 시각이다.
`checkin`은 기존 `checkin_id`, `occurrence_id`, `status`, nullable `taken_at`,
`revision`, `corrected`에 현재 Check-in의 `updated_at`을 더한 리포트 전용 DTO다.
기존 날짜별 조회·Check-in 쓰기 응답은 변경하지 않는다.
`corrected = revision > 1`이며 이전 revision을 횟수에 중복 가산하지 않는다.

Repository의 한 SELECT에서 occurrence와 현재 Check-in을 함께 읽고, 그 행들로
records와 counts·두 비율을 만든다. 별도 집계 SELECT와 records 조회 사이의 정정으로
한 응답 안에 서로 다른 수치가 생기지 않게 한다.

## 확장 및 병렬 작업 경계

Barrier·증상·Support는 이번 DTO에서 생략한다. 미구현을 빈 배열이나 0건으로 표현하지 않는다.
약명·용량 등 과거 약 snapshot 표시 계약은 #474를 소비하는 후속 연결이며 중복 구현하지 않는다.
#469 Push의 구독·전송·계정 전환 API/DB/Worker는 수정하지 않는다.
기존 B1/B3 상태 전이, deadline, Schedule/Check-in 쓰기, Notification 처리도 변경하지 않는다.

## 구현 시 필수 검증

1. 7일·30일 양 끝과 바깥 날짜, UTC/KST 자정, 오늘 기본값과 미래/잘못된 query.
2. TAKEN=2, NOT_TAKEN=1, UNCONFIRMED=1: 복용률 66.7%, 기록 확인률 75.0%.
3. 빈 집합, UNCONFIRMED만, PENDING/CANCELLED만, 분모 0과 반올림 tie.
4. deadline 직전/동일/이후 및 Scheduler 지연: 조회가 쓰기를 하지 않고 pending 지연을 드러냄.
5. UNCONFIRMED→TAKEN 및 TAKEN→NOT_TAKEN 정정: 원래 날짜에 최신 한 건만 집계.
6. 현재·과거 version, 종료/취소 schedule의 보존 기록, 타 사용자·SELF 미존재.
7. 실제 PostgreSQL 소유권 join·기간 조회 및 반환 records와 수계산 counts의 일치.
8. OpenAPI·DTO 계약 테스트와 위 사례들의 비식별 합성 Frontend fixture.
9. CONTRIBUTING.md 필수 검사. DB runner는 #469와 동일 test DB를 동시에 재생성하지 않음.

## 구현 및 리뷰 상태

사용자는 기간·과거 version·제외 상태·0분모·반올림을 포함한 제안대로 구현 진행을 확인했다.
Backend/Security(송은영), Frontend(남한솔)의 지정 리뷰는 대기 중이다.
[검증 기록](../../validation/track-b/issue-419-medication-report.md)과
[Frontend 합성 fixture](../../validation/track-b/issue-419-report-fixtures.json)를 함께 검토한다.
기존 일정·Check-in DTO와 DB는 변경하지 않았다. Current 승격·병합·외부 승인·Production 공개는 별도다.
