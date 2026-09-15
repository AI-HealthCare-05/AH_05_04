# Track C ActionPlan 조회·완료·취소 v1 — #617

- 상태: Proposed / 구현·책임 리뷰 대상. Current 승격·Production 승인 아님.
- 구현: 권가빈 (@hazelnutflavoured). 단일 책임 리뷰어: 김지혜 (@Jye-rookie).
- 검토 범위: Track C Backend·API/DTO·Transaction·Security·Frontend 소비 경계.
- 근거: [PD-617](../../governance/decisions/2026-09-16-track-c-plan-lifecycle-617.md),
  [생성 계약](track-c-support-plan-api-194.md), [Check-in target](../targets/post-mvp-1/checkin-v1.md).

## 범위와 상태

기존 `ACTIVE`, `COMPLETED`, `CANCELLED`를 그대로 사용한다. 한 Plan은 ACTIVE에서
COMPLETED 또는 CANCELLED로 단 한 번만 전환한다. 종료 상태 수정·재활성화·config 변경·follow-up은 제외한다.
ABA(종료 후 ACTIVE 복귀)가 없으므로 새 revision column 없이 잠금 후 ACTIVE 검사가 동시 수정 충돌을 검출한다.
기존 생성 응답과 암호화된 과거 멱등 snapshot은 변경하지 않는다. DB migration은 없다.

## GET /api/v1/support-action-plans/{id}

- operationId: `support-action-plan.get`.
- 인증 및 Plan → Barrier → Check-in → occurrence → schedule → prescription의 SELF 소유권 확인.
- 미존재·타인은 모두 `404 ACTION_PLAN_NOT_FOUND`.
- 200 응답은 기존 `SupportActionPlanResponse`: `data` 안에 `support_action_plan_id`,
  `barrier_response_id`, `support_code`, `rule_version`, `copy_version`, `action_config_snapshot`,
  `status`, `created_at`, nullable `completed_at`, nullable `cancelled_at`.
- 저장 당시 snapshot과 현재 저장 상태를 단일 SQL로 반환한다. 행 잠금·멱등 key·쓰기·활성 config 파일 로딩 없음.
- Check-in/Safety/Barrier가 바뀌어도 소유 이력 조회는 가능하다. ACTIVE라는 조회 결과가 실행 허가는 아니다.
  현재 지원 Flow 검사와 과거 Copy 본문 재구성은 하지 않는다. UI는 조회만으로 지원 행동을 자동 실행하지 않는다.

## PATCH /api/v1/support-action-plans/{id}

- operationId: `support-action-plan.patch`.
- `Idempotency-Key` 필수, 기존 16~255 ASCII 형식. 성공 200 + 기존 Plan data envelope.
- body는 아래 두 필드만 허용하며 모두 필수다.

| 필드 | 타입·의미 |
| --- | --- |
| status | `COMPLETED` 또는 `CANCELLED` |
| confirmed | JSON boolean true만 허용. false·숫자 1·문자열·null 불가 |

`confirmed=true`는 사용자가 해당 지원 행동을 마쳤거나 취소하겠다고 명시 확인했다는 선언이다.
서버가 실제 화면 클릭이나 행동 수행을 증명하지 않는다. Frontend는 자동 제출하지 않는다.
REMINDER_SETUP에서는 기존 일정 확인 또는 일정 저장 성공 **뒤** 명시 완료 확인으로 제출한다.
Plan 생성·화면 진입만으로 완료하지 않는다. 이 API는 일정/알림/복용 결과/처방을 변경하지 않으며,
약별 조건 검증이나 실제 일정 저장은 기존 Track B API 책임이다. 완료는 지원 행동의 사용자 확인 기록이다.

### 원자성과 검증 순서

1. SELF 소유권 확인 후 공통 SYNC_MUTATION 멱등 replay/충돌 처리.
2. 새 mutation만 소유 Plan과 불변 Barrier 부모를 재조회.
3. 소유 Check-in → 현재 Check-in revision의 최신 Safety → 최신 Barrier → 대상 Plan 순서로 행 잠금.
   Plan은 populate_existing으로 새로 읽어 대기 중 완료/자동 취소된 상태를 놓치지 않는다.
4. Plan이 ACTIVE가 아니면 `409 ACTION_PLAN_STATE_CONFLICT`. 같은 상태라도 새 key 요청은 충돌이다.
5. COMPLETED만 현재 Check-in=NOT_TAKEN 및 원래 revision, 최신 ROUTINE/NORMAL Safety,
   최신 Barrier 및 그 Safety 결속을 생성 경로와 동일하게 재검증한다.
6. COMPLETED는 completed_at, CANCELLED는 cancelled_at을 서버 UTC로 한 번 기록한다.
   다른 timestamp는 null로 유지한다. snapshot/version/created_at은 보존한다.
7. 상태와 암호화된 최초 성공 응답 snapshot을 같은 transaction에 저장한다. 실패 시 모두 rollback.

취소는 더 이상 현재가 아닌 흐름에서도 소유 ACTIVE Plan에 허용한다. Safety나 Check-in 정정으로
이미 자동 취소됐다면 4단계에서 충돌한다. 완료 이력은 이후 정정으로 취소하지 않는 기존 #195 기준을 유지한다.
새 Safety/Barrier 또는 Check-in 정정은 같은 Check-in 잠금을 공유한다. 먼저 완료됐다면 완료 이력이
보존되고, 먼저 자동 취소됐다면 후속 완료는 충돌한다. Barrier 또는 ROUTINE Safety만 먼저 정정된 경우는
완료 시 BARRIER_FLOW_STALE로 차단하고 사용자 취소는 허용한다.

### 멱등성

scope는 `(user_id, support-action-plan.patch, plan_id, key_hmac)`.
같은 key/body는 최초 HTTP status/body를 그대로 재현하고 현재 Safety·Plan 상태를 재검증하지 않는다.
이는 과거 확인 결과의 재현이다. 현재 상태는 GET으로 확인한다. 같은 key/다른 body는
`409 IDEMPOTENCY_KEY_CONFLICT`. 실패 응답은 저장하지 않는다.

### 오류·공통 응답

| HTTP | code |
| --- | --- |
| 400 | IDEMPOTENCY_KEY_REQUIRED / IDEMPOTENCY_KEY_INVALID |
| 401 | 기존 인증 오류 |
| 404 | ACTION_PLAN_NOT_FOUND |
| 409 | ACTION_PLAN_STATE_CONFLICT / CHECKIN_FLOW_STALE / SAFETY_FLOW_PRECEDES_SUPPORT / BARRIER_FLOW_STALE / IDEMPOTENCY_KEY_CONFLICT |
| 422 | VALIDATION_FAILED |
| 503 | IDEMPOTENCY_RESPONSE_TOO_LARGE |

오류는 기존 code/message/details/trace_id 형식. 성공·오류 모두 `Cache-Control: no-store`.
새 의료 판정·Copy·Provider 호출·공개 flag는 없다. `PUBLIC_TRACK_C=false`와 외부 승인 게이트를 유지한다.

검증: [#617 검증 기록](../../testing/track-c-plan-lifecycle-617.md).
