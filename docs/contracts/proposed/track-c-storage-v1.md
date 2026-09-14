# Track C C1 저장 기반 v1 — #192 / PR #310

- 상태: **Proposed — 구현 PR 리뷰 대상**, 공개 API 또는 HandlerConfig 승인 아님
- 구현: 김지혜 (`@Jye-rookie`), 담당 리뷰어: 송은영 (`@phina-io`)
- 기준: `develop` `f10ca016`, [Check-in/Barrier target](../targets/post-mvp-1/checkin-v1.md)
- 결정 근거: [C1 저장 구체화](../../governance/decisions/2026-09-13-track-c-storage-192.md)
- 검증: [C1 PostgreSQL 검증](../../testing/track-c-storage-192.md)

## 범위

#199~#201이 제공하는 실제 Check-in 부모 구조에 C1 저장 기반을 연결한다.
공개 Router/DTO, Safety 판정, Barrier mutation, Support Handler 실행, 동기 멱등 처리,
Check-in 정정 시 Track C 무효화는 이 저장 기반의 완료 주장에 포함하지 않는다.
현재 서비스에 쓰기 경로를 등록하지 않으며 Track C 공개 게이트는 닫힌 상태를 유지한다.

## 저장 구조

| 테이블 | 필수 내용 | 참조·중복 기준 |
| --- | --- | --- |
| `safety_assessment` | Check-in ID·당시 revision, assessment revision, symptom_codes 배열, response_level·safety_disposition, message_code·copy_version·source_version, 생성 시각 | Check-in ID RESTRICT FK; `(medication_checkin_id, checkin_revision, revision)` unique |
| `barrier_response` | Check-in ID·당시 revision, 근거 Safety ID, Barrier revision, response_status·nullable barrier_code, 생성 시각 | `(safety_assessment_id, medication_checkin_id, checkin_revision)` 복합 FK; Check-in revision 안에서 Barrier revision unique |
| `support_action_plan` | Barrier ID, support_code·rule_version·copy_version·action_config_snapshot, 상태·생성/완료/취소 시각 | Barrier RESTRICT FK; Barrier당 ACTIVE 최대 1개 partial unique |
| `action_plan_followup` | Plan ID, response, revision, 생성/수정 시각 | Plan RESTRICT FK; Plan당 논리 응답 1개 |
| `action_plan_followup_audit` | Follow-up ID, 이전/이후 response·revision, changed_by·changed_at | Follow-up·User RESTRICT FK; `(followup_id, to_revision)` unique |

ID는 기존 부모와 같은 CHAR(36) UUID, revision은 양수 정수다. 코드/상태는
기존 Draft의 문자열 Enum을 사용하고 일반 CHECK로 잘못된 값을 거부한다.
문구 코드·버전 필드는 VARCHAR(100), 날짜는 timezone-aware timestamp다.

### Revision과 소유권

Check-in은 `occurrence_id`당 현재 행 하나이며 정정 시 같은 ID의 revision을 증가시킨다.
따라서 이력의 `checkin_revision`을 현재 Check-in revision에 FK로 연결하지 않는다.
Safety·Barrier의 revision은 **같은 Check-in revision 안에서** 증가한다.
과거 결과 조회는 허용하지만, 그 결과를 현재 flow로 사용할 수 있다는 뜻은 아니다.

직접 `profile_id` 컬럼을 중복 추가하지 않는다. `TrackCStorageRepository`는
Safety/Barrier → Check-in → Occurrence → Schedule → Version Medication →
Prescription Version → Prescription → SELF Profile 경로로 소유권을 검사한다.
ActionPlan·Follow-up도 같은 Barrier 부모 경로를 사용한다. 타인 ID와 없는 ID 모두
`None`이며, HTTP 404 변환은 후속 API 계층의 책임이다. 공개 조회 API는 추가하지 않는다.

### 상태와 이력

- Safety 일반 조합: ROUTINE/NORMAL, URGENT/URGENT_ROUTED,
  EMERGENCY/EMERGENCY_ROUTED, UNKNOWN/UNKNOWN_RISK. BLOCKED_ACTION은 승인 target의
  정책 차단 결과 저장을 허용하며 실제 차단 근거 판정은 후속 Python Service 책임이다.
- Barrier: ANSWERED는 코드 필수, DECLINED는 코드 NULL. 미제출은 행 없음.
- ACTIVE Plan에는 완료/취소 시각이 없고, COMPLETED에는 완료 시각만,
  CANCELLED에는 취소 시각만 존재한다. 종료된 Plan 이력을 유지하며 새 ACTIVE Plan을 허용한다.
- Follow-up은 HELPED/NOT_HELPED/NOT_SURE만 저장한다. “나중에”는 행 없음.
  감사 revision은 `to_revision = from_revision + 1`이어야 한다.
- `symptom_codes`는 JSON 배열, `action_config_snapshot`은 JSON 객체다.
  JSON 모양 검사만으로 승인된 증상 코드나 Handler 설정이라는 의미를 부여하지 않는다.

## Python 쓰기 계층의 후속 연결 조건

이 PR의 repository는 **소유권 검증 조회만** 제공한다. Safety·Barrier·감사의 append-only,
현재 NOT_TAKEN/revision 검증, 최신 ROUTINE Safety 확인, 코드 목록·Handler config 검증,
Plan 종료 후 재활성화 금지, follow-up 상태별 허용 여부·정정 감사 원자성은
#193~#195의 명시적 Service/Repository transaction에서 연결해야 한다.
이 조건을 충족하기 전에 C1 테이블을 사용자 mutation에 연결하지 않는다.
DB CHECK가 위 lifecycle 전체를 집행하는 것으로 해석하지 않는다.

잠금 순서는 기존 목표인
`MEDICATION_CHECKIN → SAFETY_ASSESSMENT → BARRIER_RESPONSE → SUPPORT_ACTION_PLAN`을 유지한다.
#195는 Track B와 같은 session/transaction에서 처리해야 하며 이 PR은 기존 no-op port를 교체하지 않는다.

## HandlerConfig 경계

PM이 확인한 Plan snapshot 필드 4개만 저장한다. Handler별 필수 JSON 키, config schema version,
config 독립 테이블, 운영 seed와 실행 adapter를 임의로 만들지 않는다.
따라서 이 구현을 HandlerConfig 저장 완료나 #192 전체 종료로 선언하지 않는다.

## Migration

`192a1b2c3d4e`는 최신 병합 head `166f30415263` 뒤에 추가한다.
적용된 migration은 수정하지 않고 Track B 기존 행을 보정하거나 삭제하지 않는다.
Downgrade는 5개 테이블을 같은 transaction에서 잠근 뒤 하나라도 데이터가 있으면
전체 중단한다. 빈 DB만 역순 삭제한다. 기존 데이터는 forward-fix 대상이다.
신규 RLS·DB Trigger·업무용 DB 함수는 없다.
