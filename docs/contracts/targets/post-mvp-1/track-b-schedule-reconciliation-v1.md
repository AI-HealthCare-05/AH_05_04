# Track B 일정 정합화 v1

| 항목 | 값 |
| --- | --- |
| 상태 | **Approved target** — PR #424 양 도메인 승인; #423 DB 작업 브랜치 구현·리뷰/머지 대기, 일정 API #202·실제 알림 연동 #203 별도 |
| 결정 정본 | [PD-417-20260910](../../../governance/decisions/2026-09-10-track-b-schedule-contract.md) |
| 기존 목표 | [Check-in v1](./checkin-v1.md), [멱등성 v1](./idempotency-v1.md) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰어 | 송은영 (`phina-io`) — Backend·DB·Security; 남한솔 (`solia142`) — Frontend |

## 1. 원본·target·현재 구현 차이

원본 절·열람 해시와 주장별 `target 교차 확인됨 / 열람본만` 판정은
[Decision의 교차 확인 표](../../../governance/decisions/2026-09-10-track-b-schedule-contract.md)에 기록한다.
아래 원본 인용 전체가 repository target으로 확인됐다는 의미는 아니다. 특히 물리 time status·schedule
audit·종료 revision 증가는 열람본만의 세부 요구로 분리해 승인했다.
이 표의 현재 구현은 Decision에 고정한 develop 기준이다.

| 항목 | 원본 / repository target | 현재 구현 근거 | 승인 결과 / 후속 |
| --- | --- | --- | --- |
| setup reason | Freeze §7, Track B §3.1: 4값; target 신규 값·우선순위는 TBD | B1/B2에는 공개 조회 DTO 없음; `docs/api.md`의 B API는 목표 목록 | §2의 승인된 5값·우선순위, #202 구현 |
| 전체 INACTIVE 경계 | 원본은 모두 INACTIVE이고 pending 없음 | 취소해도 과거 pending은 남을 수 있어 원본 aggregate에 빈 분기가 있음 | §2에서 모두 INACTIVE이면 전체 INACTIVE, 과거 pending 별도 표시를 명시적 delta로 승인 |
| 일정 감사 | Freeze §7, Track B §2·3.2: 이전/새 snapshot과 revision, actor/time | [B1 모델](../../../../backend/app/models/medication_schedules.py)에 schedule audit 없음. B3 `CheckinAudit`는 Check-in 전용 | §3의 새 audit 저장, #423 |
| time retire | 원본은 물리 `ACTIVE/RETIRED`; target은 revision별 보존만 요약 | B1 time은 status 없이 revision별 row 보존, B2는 현재 revision으로 조회 | §3의 파생 retire 승인, time status migration 불필요 |
| 종료 revision | 원본은 조건부 revision+audit | [B2 repository](../../../../backend/app/repositories/medication_schedule_repository.py)의 `mark_expired_schedules_ended`는 status만 변경 | §4의 한 단계 증가와 audit, #423 |
| 생성·변경·취소·재활성화 | Track B §3.2: 동일 row의 새 revision·audit·horizon | B1 생성과 time 추가 존재, 변경·취소 전체 원자적 API 미완성 | §4–5 저장 기반 #423, API #202 |
| 소유권·시간대 | 원본은 직접 user/version/timezone 필드 | B1은 SELF parent chain, 고정 KST 서비스 설정. [schema](../../../data-schema.md) 참조 | 기존 chain·KST 유지; user/version/timezone 중복 컬럼 추가 안 함 |
| occurrence 보존 | 원본 `(time_id, scheduled_at)` unique, nullable `cancelled_at` | B1은 `(time_id, scheduled_local_date)` unique, revision snapshot; `cancelled_at` 없음 | KST 매일 반복의 현재 unique 유지. 취소 시각은 §3의 신규 nullable 컬럼으로 보완, #423 |
| 처방 활성화 | Track B §4: 동일 transaction의 미래 pending·미전달 알림 취소 | B2는 `scheduled_at >= effective_at` pending 취소; [서비스](../../../../backend/app/services/medication_occurrences.py)의 알림 port는 optional | §5의 동일 session 실제 adapter는 #203, API 연결은 #202 |
| 검증 범위 | 원본 §6은 revision·audit·알림 rollback까지 요구 | 기존 [B1 migration 테스트](../../../../tests/migration/test_medication_schedule_migration.py), [B2 통합 테스트](../../../../backend/app/tests/repositories/test_medication_schedule_repository_integration.py)는 unique·KST·종료 status·미래 취소 검증 | 기존 테스트 존재는 새 감사·알림 통합 검증 PASS 아님. §7 후속 증빙 필요 |

## 2. setup_reason 단일 반환

약별 `SETUP_REQUIRED`일 때 아래 첫 번째 해당 사유를 단일 non-null 값으로 반환한다.
`READY` 또는 `INACTIVE` 항목은 `setup_reason=null`이다. `CANCELLED/ENDED` 일정은 명시적 재활성화 전
`INACTIVE`이며 단순히 active schedule이 없다는 이유로 확인 요청 상태로 되돌리지 않는다.

| 우선순위 | 값 | 판정 |
| ---: | --- | --- |
| 1 | `UNSUPPORTED_SCHEDULE_PATTERN` | 격일·요일별·필요시 등 매일 동일 시각 반복으로 표현 불가 |
| 2 | `MISSING_START_DATE` | 해당 version의 정확한 시작일 입력이 없음 |
| 3 | `MISSING_EXACT_TIME` | 해당 version의 정확한 시각 집합 입력이 없음 |
| 4 | `MISSING_DURATION_DECISION` | 종료일을 정할 기간/종료일 또는 명시적 OPEN_ENDED 선택이 없음 |
| 5 | `USER_CONFIRMATION_REQUIRED` | 1–4는 충족하지만 해당 version의 일정 PUT 확인이 아직 없음 |

값의 존재와 사용자 확인은 별개다. `timing_text`, `frequency_per_day`, 처방 확정일에서 시작일·시각을
추정하지 않는다. 현재 snapshot에 정확한 값의 저장 경로가 없다면 없는 값으로 판정한다. 새 필드나 NLP
추출기를 이 결정만으로 추가하지 않는다. `duration_days`가 있으면 사용자가 확인할 종료일 후보는
`start+duration-1`이고, null 기간을 묵시적 OPEN_ENDED 확인으로 취급하지 않는다.
Frontend는 Backend reason을 표시·분기하고 우선순위를 재계산하지 않는다.
`NO_ACTIVE_PRESCRIPTION`은 전체 상태이며 reason enum에 넣지 않는다.

전체 `READY/PARTIAL/SETUP_REQUIRED/INACTIVE/NO_ACTIVE_PRESCRIPTION`과 앞선 aggregate 우선순위는 유지한다.
마지막 분기는 **모든 약이 INACTIVE이면 전체 INACTIVE**로 정한다. 원본의 “pending 없음” 조건은
과거 pending 보존과 동시에 만족하지 않을 수 있으므로 제거하는 명시적 delta다. 이때 과거 pending도
응답 목록에 남기고 기한·Check-in 상태로 표시한다. INACTIVE는 과거 Check-in 금지나 목록 비움을 뜻하지
않는다. `PARTIAL`에서도 ready 약의 occurrence를 숨기지 않는다. 날짜별 fixture는 #202에서 남한솔이 검토한다.
새 version은 일정·시각을 자동 복사하지 않고 다시 설정한다. `NEW_MEDICATION`/`NEW_PRESCRIPTION_VERSION`
reason은 추가하지 않는다. HTTP 정상 미설정 응답은 `200`이다.

## 3. 감사 저장과 time 보존

`medication_schedule_audit`를 새 테이블로 정한다. 기존 B3 `checkin_audit`에 섞지 않는다.

| 필드 | 승인 타입·제약 |
| --- | --- |
| `id` | UUID PK |
| `medication_schedule_id` | UUID FK → schedule, NOT NULL, 이력 삭제를 막는 RESTRICT |
| `from_revision`, `to_revision` | integer NOT NULL; `from_revision >= 0`, `to_revision = from_revision + 1`; `(medication_schedule_id, to_revision)` unique |
| `before_snapshot` | nullable JSONB, 최초 0→1 생성만 null |
| `after_snapshot` | JSONB NOT NULL |
| `changed_by` | nullable UUID FK → user; 사용자 mutation은 인증 user, Scheduler는 null |
| `change_source` | `USER` 또는 `SCHEDULER`, NOT NULL; USER면 changed_by 필수, SCHEDULER면 null |
| `changed_at` | UTC timestamptz NOT NULL, 해당 transaction에서 고정한 유효 시각 |

snapshot은 `start_local_date`, `end_mode`, nullable `end_local_date`, 정렬된 중복 없는 `local_times`,
`status`, `source`만 가진다. 날짜는 ISO date, 시각은 KST `HH:mm`으로 고정한다. 시각 집합은 해당 변경의
전후 설정값이며 취소/종료 시에도 직전 설정값을 보존한다. 취소/종료 revision에는 time이 없으므로
해당 revision 이하의 마지막 time 집합을 설정 snapshot으로 사용하고, 이 집합으로 occurrence를 생성하지는 않는다. 처방 원문·약명·용량·자유 텍스트는 넣지 않는다.
Service에서 snapshot shape와 revision 연속성을 검증하고 현재 row와 같은 transaction에서 append한다.
일반 수정·조회 API로 audit을 수정하거나 공개하지 않는다. 계정 삭제·legal hold 정책은
[Privacy 기준](../../../privacy-safety.md)을 따른다.

기존 time row는 변경·삭제하지 않는다. 생성 가능한 time은 schedule이 `ACTIVE`이고 `time.schedule_revision == schedule.revision`인 row다. 나머지는 retire로 해석한다. 취소·종료 revision에
time row를 새로 만들 필요는 없으며 재활성화는 새 revision time을 만든다. 이것은 원본의 물리 status
컬럼 요구를 바꾸는 PD-417의 승인 delta이며 원본 전체의 준수 완료를 뜻하지 않는다.

occurrence의 `cancelled_at`은 신규 취소 시 유효 시각을 보존하는 nullable UTC timestamptz로 추가한다.
기존 CANCELLED row의 실제 시각은 알 수 없으므로 `updated_at`을 복사하거나 가짜 audit을 만들지 않는다.
기존 일정은 migration 당시 현재 revision·설정만 별도 migration 증빙에 baseline으로 남기고 과거 actor·
시각·변경을 재구성하지 않는다. 다음 실제 mutation부터 n→n+1 audit을 남긴다. #423은 baseline 접근·보존,
기존 null 취소 시각 구분, audit 삭제 방지와 이력이 있는 downgrade 거부를 송은영 리뷰로 검증한다.

## 4. revision 전이

| 사건 | 전제 | 결과 | time/occurrence |
| --- | --- | --- | --- |
| 최초 PUT | 일정 없음, expected=0 | ACTIVE 1, audit 0→1 | revision 1 time과 horizon 생성 |
| 변경 PUT | ACTIVE n, expected=n | ACTIVE n+1, audit | 새 time, 미래 pending 취소 후 새 horizon |
| 재활성화 PUT | CANCELLED/ENDED n, expected=n, 현재 active 처방 | 같은 row ACTIVE n+1, audit | 새 명시적 입력·새 time·horizon; 과거 재귀속 없음 |
| 취소 PATCH | ACTIVE n, expected=n | CANCELLED n+1, audit | 기존 time retire 해석, 미래 pending·미전달 알림 취소 |
| 반복 취소 PATCH | 이미 CANCELLED 또는 ENDED, expected=현재 | 현재 상태·revision 유지, audit 없음 | ENDED를 CANCELLED로 덮어쓰지 않음; 성공 snapshot 저장 |
| Scheduler 종료 | ACTIVE, DATE, end_local_date < 오늘 KST | ENDED n+1, audit, actor=SCHEDULER | 새 time 없음, 기존 occurrence·Check-in 유지 |
| rolling 재실행 | 현재 active schedule | revision·audit 변경 없음 | time/date unique로 중복 방지 |
| 처방 version 교체 | activation UoW | 이전 schedule revision·상태 유지 | 미래 pending만 취소; 이전 version은 생성 대상에서 제외 |

PUT은 새 멱등 키로 명시적으로 확인하면 payload가 같아도 한 revision을 추가한다. 동일 키·동일 hash
재전송은 현재값을 다시 비교하지 않고 최초 성공 응답을 재현하여 revision·audit·occurrence를 추가하지 않는다.
새 키의 stale expected revision은 payload가 같아도 `409 SCHEDULE_REVISION_CONFLICT`다.
소유권 없는 ID는 `404`, 소유하지만 현재 active version이 아니면 `409 PRESCRIPTION_VERSION_CONFLICT`다.
PATCH의 `ENDED` 직접 제출은 허용하지 않으며 기존 공통 validation `422 VALIDATION_FAILED`를 사용한다.

## 5. transaction과 잠금

단독 PUT/PATCH와 Scheduler는 `PRESCRIPTION → MEDICATION_SCHEDULE → MEDICATION_SCHEDULE_TIME →
MEDICATION_OCCURRENCE → NOTIFICATION_RECORD` 순서를 따른다. 존재하지 않는 schedule 최초 생성도 부모
Prescription 잠금과 version medication unique로 직렬화한다. 여러 row는 각 단계에서 PK 오름차순으로
잠그며 잠금 뒤 active version·revision·상태를 다시 확인한다. audit append는 잠근 schedule의 변경에 속한다.
B2의 단일 JOIN `FOR UPDATE`는 표기만으로 전역 잠금 순서가 증명되지 않으므로 #423에서 경합 검증한다.

처방 활성화는 기존 `PRESCRIPTION → CHAT_SESSION(해당 시) → AI_JOB → B domain rows → OUTBOX` 순서를
유지한다. B port는 AI_JOB 단계 뒤 동일 session/transaction을 사용하며 내부 commit하지 않는다.

일정 write의 처리 순서는 다음과 같다.

1. 인증·입력·SELF 소유권 확인과 공통 멱등성 처리. 동일 hash의 성공 snapshot 재현은 revision/현재 상태 검사보다 앞선다.
2. 위 순서로 부모와 대상 row를 잠그고 active version·expected revision을 재검증한다. transaction당 하나의 aware UTC `effective_at`을 고정한다.
3. 변경 전 snapshot을 확보하고 새 일정 revision·time·audit을 저장한다.
4. `PENDING AND scheduled_at >= effective_at AND confirmation_deadline_at > effective_at`이며 Check-in이 없는 대상만 취소하고 cancelled_at을 기록한다. equality는 미래 취소에 포함한다.
5. 취소한 occurrence ID로 #203 동기 취소 port를 호출한다. 같은 session에서 미전달 알림을 취소한다.
6. PUT은 시작/종료일로 자른 오늘 KST부터 14개 local date horizon 중 `scheduled_at >= effective_at`만 새 revision으로 생성한다. 오늘 이미 지난 시각을 수정 때문에 재생성하지 않는다. 이후 rolling도 현재 revision을 생성한 USER audit의 `changed_at` 하한을 적용해 같은 날 과거 시각을 다시 만들지 않는다. 기존 audit 없는 baseline 일정은 기존 생성 기준을 유지한다. 14일 길이·unique·deadline 계산은 유지하며 audit 하한 참조는 #423의 B2 보완 범위다.
7. canonical 성공 응답을 만들고 공통 암호화 멱등 snapshot과 함께 commit한다. cap 초과·audit 실패·알림 취소 실패·생성 실패는 전부 rollback한다.

기한 경과·이미 응답·effective_at 이전 occurrence, Check-in/audit, 참조된 time row와 전달된 알림은 보존한다.
종료는 전날 늦은 시각의 아직 유효한 확인 기한을 자르지 않는다. 처방 활성화도 같은 미래 취소 조건을 쓰고
새 version에는 자동 생성하지 않는다. 알림 port 미주입은 현재 B2 구현 사실이며 #203 완료 이후 정상
배선으로 허용하지 않는다. 독립 transaction·사후 이벤트 취소로 원자성을 대신하지 않는다.

## 6. #203 미전달 알림 경계

미전달은 앱 내부 공개 가능한 전달 상태로 전환되기 전이며 **미읽음과 다르다**. 전달됐지만 읽지 않은
알림을 미전달로 간주해 취소하지 않는다. #417은 Notification enum/DTO를 별도 확정하지 않는다.
#203은 실제 저장 상태·전달 시점과 이 의미의 매핑을 계약·테스트에 명시하고 양 리뷰어의 검토를 받는다.

생성/전달/재알림 경로는 occurrence를 먼저 잠그고 상태·기한을 재검증한 후 notification을 잠그거나
삽입한다. 취소와 경합해 취소가 먼저 commit되면 새 알림 생성·전달을 하지 않는다. 전달이 먼저
commit되면 전달 이력을 보존한다. 읽음 처리는 전달 여부를 소급 변경하지 않는다. 외부 Push·SMS·Email은
범위 밖이며 새 전달/retry enum은 이 문서에서 만들지 않는다.

## 7. 인계와 검증 완료 조건

| 이슈 | 병행 가능한 범위 | 의존성과 완료 증빙 | 담당 / 책임 리뷰 |
| --- | --- | --- | --- |
| #417 | 이 Decision과 차이·후속 정리 | 송은영·남한솔 승인, 원본 delta 적용 범위 확인 후 Target 반영 | 권가빈 / 송은영·남한솔 |
| [#423](https://github.com/AI-HealthCare-05/AH_05_04/issues/423) | 승인된 DB 구현 | audit·cancelled_at migration, 종료 revision·잠금 보완, baseline·rollback·DB 통합 증빙 | 권가빈 / 송은영, Frontend 의미 남한솔 |
| #202 | 명확한 Check-in API·404·공통 오류·기존 검증 | setup reason은 #417 승인에 따라 DTO/OpenAPI/fixture/계약 테스트 동기화. PUT/PATCH는 #423 완료 및 #203 취소 adapter 연결 필요. migration 제외 유지 | 권가빈 / 송은영·남한솔 |
| #203 | 명확한 알림 저장·조회·읽음 | §5–6 승인 결과로 동일 session adapter·생성/전달 경합·과거 보존 검증. Schedule migration 제외 유지 | 권가빈 / 송은영·남한솔 |
| #138 | 승인된 DTO를 소비하는 UI | reason 재계산 없음, PARTIAL·취소·재활성화·늦은 Check-in fixture | 남한솔 / 실제 구현 PR에서 별도 지정 |

후속 구현 PR은 아래 시나리오를 자동 테스트로 증명한다. 이 문서에 적힌 기대값은 승인된 기대값이며 구현 테스트 PASS 증빙은 아니다.

- reason 복수 결손의 각 우선순위, 값 완비/확인 누락, READY·INACTIVE null, 새 version 재확인, PARTIAL 보존.
- 최초/수정/재활성화/취소/반복취소/종료의 revision·audit·time 참조, same-key replay와 새 키 stale conflict.
- 종료 Scheduler 중복·PUT과 종료 경합: 한 번의 상태 전이, stale write 실패, audit 연속성.
- 유효 시각 바로 전/정각/직후, KST 자정, 23시 일정의 다음 날 확인 기한, 과거·응답 완료 보존.
- 처방 활성화 대 일정 PUT/rolling 경합, 취소 대 알림 생성/전달 경합, 취소 대 Check-in 경합.
- 알림 port/audit/occurrence 생성 실패와 멱등 snapshot cap 초과 때 도메인·audit·snapshot 전부 rollback.
- migration 단일 head·upgrade·baseline과 기존 nullable 취소 시각, 실제 이력 보유 downgrade 거부.
- SELF 교차 사용자 404, no-store, 민감 snapshot 비로그, Frontend와 OpenAPI enum/requiredness 일치.

선행 PR #424는 문서만 변경했다. 기존 코드·자동 테스트의 존재 확인과 문서 링크·render·diff 검증은 수행하되
DB/API/Frontend 및 의료 eval 실행 결과를 새 계약의 PASS 증빙으로 주장하지 않는다.

## 승인 증빙

PR [#424](https://github.com/AI-HealthCare-05/AH_05_04/pull/424)의 승인 대상은 `4fdecc8af73a62803cd970160886115d0c91be36`, develop 머지는 `015571a0a928f1146654bc5a9dacccada5b361f0`이다. 책임 리뷰 URL·UTC 시각·적용 범위는 Decision의 승인표를 따른다. 문서 승인은 DB/API 구현이나 공개 승인을 대신하지 않는다.

## #423 저장 구현·인계

[PD-423 저장 통제 보완](../../../governance/decisions/2026-09-10-schedule-audit-storage.md)은
별도 접근 제한 JSON baseline과 기존 #398 Runtime SELECT/INSERT 권한 정책의 감사 테이블 적용을 명시한다.
**DB trigger와 ORM event는 추가하지 않으며 Runtime의 직접 SQL UPDATE/DELETE/TRUNCATE도 차단한다.** 사용자 작업 지시에
따른 구현이며 이번 지정 도메인 리뷰는 별도로 필요하다. `423a1b2c3d4e`는 최신 develop의
`3984b5c6d7e8` 뒤에 연결하며 기존 B1/B3 migration을 수정하지 않는다.

#202는 `MedicationScheduleMutationService.put/cancel`을 동일 AsyncSession에서 사용한다.
입력은 `ScheduleAuditSnapshot`으로 검증된 명시적 설정, SELF user, version medication ID,
expected revision, 고정 effective_at이다. 공개 DTO 변환·frequency_per_day 일치 검증은 #202의
입력 검증 책임이며 저장 서비스는 API route/DTO를 추가하지 않는다. Snapshot의 local_times는
정렬·중복 제거된 HH:mm이고 민감 원문 필드를 허용하지 않는다.

호출자는 공통 `SyncMutationIdempotencyService`에서 성공 snapshot 재현을 먼저 처리하고, 신규
mutation과 암호화 snapshot 저장을 같은 transaction으로 commit한다. 저장 서비스는 자체 commit을
하지 않는다. Service의 OwnershipNotFound/VersionConflict/RevisionConflict 예외는 #202에서
기존 계약의 404/409로 매핑하며 새 공개 오류 코드를 만들지 않는다.

#203은 `cancel_undelivered_for_occurrences`를 같은 session으로 구현·주입해야 한다. 현재 develop에
실제 adapter가 없어 SQL 합성 adapter로 transaction rollback을 검증했다. 실제 알림 전달 상태 매핑과
취소/전달 경합은 #203에서 수행한다. #423의 테스트를 실제 알림 연동 완료로 해석하지 않는다.

[구현 검증 기록](../../../validation/issue-423-schedule-audit.md)을 참조한다. DB 분량 구현만으로
전체 목표를 `current/`로 옮기거나 일정 API·Frontend 완료를 선언하지 않는다.
