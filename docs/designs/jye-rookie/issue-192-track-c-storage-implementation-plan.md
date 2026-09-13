# #192 Track C 저장 모델·Migration 구현 계획

## 상태

- 작성 기준: `develop` commit `4038f7e`
- 구현 담당: 김지혜 (`@Jye-rookie`)
- 주 리뷰어: 송은영 (`@phina-io`)
- PM 범위 확인: 권가빈 (`@hazelnutflavoured`)
- 현재 판단: Track B #199→#200→#201 부모 모델과 HandlerConfig Decision 대기
- 현재 local Alembic head: `171c0f751206` (Track B 병합 후 재확인)
- 공개 게이트: `PUBLIC_TRACK_C=false` 유지

이 문서는 #192 구현을 시작하기 전에 확정 계약과 미확정 저장 구조를 분리한다. 현재
`develop`에는 `medication_checkin` ORM 모델과 Migration이 없으므로, 부모 테이블의
이름·식별자·revision·소유권 제약을 추정한 FK Migration은 만들지 않는다.

## 확인한 기준

- `docs/contracts/targets/post-mvp-1/checkin-v1.md`는 Approved Contract Freeze v4
  target이며 구현은 아직 완료되지 않았다.
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`는 Track C 동기 변경의
  idempotency scope를 정의하지만 현재 #192은 공개 API와 mutation service를 포함하지 않는다.
- Track C의 잠금 순서는
  `MEDICATION_CHECKIN → SAFETY_ASSESSMENT → BARRIER_RESPONSE → SUPPORT_ACTION_PLAN`이다.
- Track C 결과는 동기 도메인 결과이며 `AI_JOB`에 귀속하지 않는다.
- Safety·Barrier·일반 Support는 현재 Check-in이 `NOT_TAKEN`일 때만 허용한다.
- 실제 환자 fixture와 Production 공개는 범위 밖이다.

## 2026-09-07 담당자 확인 결과

- Track B의 `medication_checkin`은 아직 구현되지 않았으며 #199 Schedule·Occurrence,
  #200 Rolling Occurrence·중복 방지·처방 변경, #201 Check-in·Audit 순서로 진행한다.
- Check-in은 `occurrence_id`별 현재 row 하나를 유지하고 정정 시 같은 row의 revision을
  올린다. 이전 값은 별도 append-only `checkin_audit`에 보존한다.
- Check-in의 `profile_id` 직접 저장 여부와 소유권 FK 형태는 #199~#201에서 확정한다.
- `symptom_codes`는 개별 코드 검색·집계 요구가 없으므로 JSON 배열로 저장한다.
- SafetyAssessment와 BarrierResponse는 모두 revision별 append-only로 저장하며 가장 높은
  revision을 현재 상태로 해석한다.
- Barrier 하나당 `ACTIVE` ActionPlan은 최대 하나이며 PostgreSQL partial unique index로
  동시 생성을 차단한다. 기존 Plan이 `COMPLETED` 또는 `CANCELLED`면 새 Plan을 허용한다.
- ActionPlan은 `support_code`, `rule_version`, `copy_version`, 선택 당시
  `action_config_snapshot`을 보존한다. Handler별 config field와 schema version은 별도
  Product Decision 또는 Contract Freeze 전까지 구현하지 않는다.
- ActionPlan별 논리적 follow-up은 하나다. 최초 응답은 `HELPED | NOT_HELPED | NOT_SURE`이며
  “나중에 응답”은 row를 만들지 않는다. 재제출은 현재 row와 revision을 갱신하고 이전 값은
  append-only audit에 보존한다.
- 데이터가 존재하는 downgrade는 중단하고 forward-fix만 허용한다.

## 확정 가능한 Enum

| 영역 | Enum | 값 |
| --- | --- | --- |
| Safety | `response_level` | `ROUTINE`, `URGENT`, `EMERGENCY`, `UNKNOWN` |
| Safety | `safety_disposition` | `NORMAL`, `URGENT_ROUTED`, `EMERGENCY_ROUTED`, `BLOCKED_ACTION`, `UNKNOWN_RISK` |
| Barrier | `response_status` | `ANSWERED`, `DECLINED` |
| Barrier | `barrier_code` | `FORGOT`, `SCHEDULE_OR_TRAVEL`, `INSTRUCTIONS_UNCLEAR`, `NEED_DOUBT`, `MEDICATION_CONCERN`, `ACCESS_OR_COST` |
| ActionPlan | `status` | `ACTIVE`, `COMPLETED`, `CANCELLED` |
| Follow-up | `response` | `HELPED`, `NOT_HELPED`, `NOT_SURE` |
| Support | `support_code` | `REMINDER_SETUP`, `ROUTINE_OR_TRAVEL_PLAN`, `INSTRUCTION_REVIEW`, `PURPOSE_REVIEW`, `MEDICATION_CONCERN_GUIDANCE`, `ACCESS_SUPPORT` |

`UNKNOWN`, `USER_DECLINED`, `uncertain`은 Barrier의 건너뛰기 값으로 추가하지 않는다.
Barrier의 명시적 거절은 `response_status=DECLINED`과 `barrier_code=null`로 저장하며,
단계에 진입하지 않거나 제출하지 않은 경우에는 row를 만들지 않는다.

## 테이블별 최소 계약과 미확정 항목

### `safety_assessment`

계약에서 확인되는 값:

- `id`
- `medication_checkin_id`
- `checkin_revision`
- 구조화된 `symptom_codes`
- `response_level`
- `safety_disposition`
- `message_code`
- `copy_version`
- `source_version`
- `revision`
- 생성 시각

필요한 제약:

- Check-in revision별 assessment 이력 보존
- 최신 revision 하나만 현재 흐름 판단에 사용
- `ROUTINE → NORMAL`, `URGENT → URGENT_ROUTED`,
  `EMERGENCY → EMERGENCY_ROUTED`, `UNKNOWN → UNKNOWN_RISK` 조합 검증
- 정책 검증으로 행동을 차단한 경우에만 `BLOCKED_ACTION` 허용
- `revision > 0`, `checkin_revision > 0`

구현 전 확정할 항목:

- 동일 Check-in revision에서 assessment revision을 식별하는 unique key
- `profile_id`를 직접 저장해 복합 FK를 만들지 부모 chain 조회로만 검증할지

확정된 저장 방식:

- `symptom_codes`는 JSON 배열로 저장한다.
- assessment는 append-only이며 가장 높은 revision을 현재 결과로 계산한다.

### `barrier_response`

계약에서 확인되는 값:

- `id`
- `medication_checkin_id`
- `checkin_revision`
- `response_status`
- nullable `barrier_code`
- `revision`
- 생성·수정 시각

필수 CHECK 후보:

```sql
(response_status = 'ANSWERED' AND barrier_code IS NOT NULL)
OR
(response_status = 'DECLINED' AND barrier_code IS NULL)
```

구현 전 확정할 항목:

- Barrier가 근거로 삼은 `safety_assessment_id`를 직접 FK로 고정할지
- append-only Barrier의 revision별 unique key

확정된 저장 방식:

- Barrier도 Safety와 동일하게 revision별 append-only로 저장한다.
- 별도 현재 row를 수정하지 않고 가장 높은 revision을 현재 응답으로 계산한다.

### `support_action_plan`

계약에서 확인되는 값:

- `id`
- `barrier_response_id`
- 선택한 `support_code`
- 선택 당시 rule·copy version snapshot
- `status=ACTIVE|COMPLETED|CANCELLED`
- 생성·완료·취소 시각

필요한 제약:

- 취소되거나 완료된 ActionPlan을 다시 `ACTIVE`로 덮어쓰지 않는 이력 보존
- 부모 Check-in revision이 바뀌면 과거 row를 보존하고 활성 계획만 취소
- Barrier별 `status='ACTIVE'` Plan을 하나로 제한하는 PostgreSQL partial unique index

구현 전 확정할 항목:

- 저장할 HandlerConfig의 필드·타입·version
- ActionPlan에 snapshot할 rule·copy·rationale·action config의 정확한 컬럼

확정된 저장 방식:

- `support_code`, `rule_version`, `copy_version`, `action_config_snapshot`을 보존한다.
- 기존 Plan이 `COMPLETED` 또는 `CANCELLED`일 때만 같은 Barrier에 새 Plan을 만들 수 있다.

### ActionPlan follow-up

계약에서 확인되는 값:

- `support_action_plan_id`
- `response=HELPED|NOT_HELPED|NOT_SURE`
- 생성 시각

구현 전 확정할 항목:

- 테이블 이름
- ActionPlan 상태와 follow-up response의 허용 조합

확정된 저장 방식:

- ActionPlan별 현재 follow-up row는 최대 하나다.
- 재제출은 현재 row와 revision을 갱신하고 이전 값은 별도 append-only audit에 보존한다.
- 동일 멱등 키 재전송은 최초 성공 응답을 재현하고 신규 키 수정은
  `expected_revision`을 검사한다.

## 소유권과 FK 전략

Issue는 SELF `profile_id` 기반 소유권 chain을 요구한다. 현재 저장소는 부모 테이블에
`UniqueConstraint("id", "profile_id")`를 두고 자식이 복합 FK로 동일 profile을 강제하는
패턴을 사용한다. #192에서도 이 패턴을 우선 검토하되, Track B가 다음 계약을 실제 모델과
Migration으로 제공한 뒤 정확히 맞춘다.

1. `medication_checkin.id`의 물리 타입
2. `medication_checkin.profile_id` 직접 저장 여부
3. `(id, profile_id)` unique 제약 이름
4. Check-in의 현재 `revision` 타입과 CHECK
5. 삭제 정책과 과거 감사 이력 보존 정책

Track B 계약이 단일 `medication_checkin_id` FK만 승인한다면 #192에서 임의로 `profile_id`
컬럼이나 복합 FK를 추가하지 않고, 별도 Decision을 요청한다.

## Migration 순서

Track B Migration이 병합된 뒤 다음 순서로 구현한다.

1. 최신 Alembic head와 Track B revision 확인
2. Track C Enum을 `native_enum=False` 문자열 Enum으로 모델링
3. `safety_assessment` 생성
4. `barrier_response` 생성
5. 승인된 HandlerConfig 저장 구조 생성
6. `support_action_plan` 생성
7. ActionPlan follow-up 생성
8. FK·unique·CHECK·index 이름을 ORM과 Migration에서 일치
9. `backend/app/models/__init__.py`에 모델 등록
10. `docs/data-schema.md`와 Migration 검증 문서 갱신

Migration은 중복 실행을 정상 동작으로 정의하지 않는다. Alembic은 적용된 revision을
기록하므로 두 번째 `upgrade head`가 스키마를 다시 변경하지 않는 것을 검증한다.

## Downgrade 기준

Issue의 “rollback 시 기존 데이터 보존”은 테이블을 제거하는 일반 downgrade와 동시에
만족할 수 없다. 기존 Migration의 안전 기준과 동일하게 Track C row가 하나라도 존재하면
`RuntimeError`로 downgrade를 중단하고 forward-fix만 허용한다. 데이터가 없는 환경에서만
승인된 downgrade가 Track C 테이블을 제거할 수 있다.

## 테스트 계획

### ORM·제약

- 모든 허용 Enum 저장
- 알 수 없는 Enum 거부
- `revision <= 0`, `checkin_revision <= 0` 거부
- Barrier `ANSWERED + null` 거부
- Barrier `DECLINED + barrier_code` 거부
- 부모 없는 FK 거부
- 다른 SELF profile의 부모를 연결하는 복합 FK 거부
- 같은 revision 또는 활성 row의 중복 거부
- 실제 환자정보가 없는 UUID와 합성 코드만 사용

### Migration

- fresh DB `upgrade head`
- develop 기존 head에서 `upgrade head`
- 두 번째 `upgrade head`가 변경 없이 성공
- FK·unique·CHECK·index의 실제 PostgreSQL catalog 확인
- 데이터가 없는 승인 downgrade 또는 데이터 존재 시 안전한 중단
- downgrade 중단 뒤 기존 데이터 보존 확인

### 명령

```bash
uv run --group app --group dev pytest tests/migration -q
uv run --group app --group dev pytest backend/app/tests -q
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
git diff --check
```

## 구현 전 확인 요청

주 리뷰어와 PM에게 다음 항목을 한 번에 확인한다.

1. Track B Check-in 모델·Migration PR과 확정 revision
2. Check-in의 `profile_id`, `(id, profile_id)` unique, revision 물리 계약
3. Safety·Barrier의 revision별 unique key
4. ActionPlan에 snapshot할 HandlerConfig 상세 필드·schema version
5. ActionPlan 상태와 follow-up response의 허용 조합

## 시작 조건

다음 조건이 충족되면 모델과 Migration 구현을 시작한다.

- Track B Check-in ID·revision·소유권 계약이 실제 모델과 Migration으로 병합됨
- HandlerConfig 저장 구조가 Decision 또는 Contract Freeze에 기록됨
- 위의 남은 미확정 항목에 대한 주 리뷰어와 PM 답변이 Issue에 남음
- 새 브랜치를 답변 시점의 최신 `develop`에서 다시 정렬함
