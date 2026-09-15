# #192 HandlerConfig 구체안

- 상태: **Proposed / 제품 승인·운영 파일 구현 완료 — 담당 기술 리뷰 대기**
- 작성 기준: PR #310에서 병합된 #192 C1 저장 기반
- 구현 제안: 김지혜. 담당 기술 리뷰: 송은영. 제품 범위 확인: 권가빈. 화면 소비 확인: 남한솔.

## 1. 제안의 핵심

**승인된 지원 설정은 버전별 저장소 파일로 관리하고, 사용자가 선택한 설정은 기존
`support_action_plan.action_config_snapshot` JSONB에 복사해 보존한다.**
별도 HandlerConfig DB 테이블이나 운영 관리 API는 현재 요구에서 필요성이 확인되지 않아 추가하지 않는 안이다.
`support_code`로 처리 분기를 선택하며 별도 handler 이름, 동적 import, Plugin/Registry 구조를 만들지 않는다.

#310의 저장 구조를 바꾸지 않고 Python에서 설정별 필드·타입·버전·참조를 검증한다.
기존 DB의 JSON 객체 CHECK는 유지하되, JSON 내부 업무 규칙은 Service에서 관리한다.
신규 RLS·DB Trigger·Stored Procedure·업무용 DB 함수는 도입하지 않는다.

## 2. 기존 근거와 이번 제안의 구분

| 구분 | 내용 | 근거 |
| --- | --- | --- |
| 기존 목표 | 6개 support_code, Barrier 대응, priority 정렬·최대 2개 | [Check-in target](../targets/post-mvp-1/checkin-v1.md) |
| 기존 합의 기록 | support_code·rule_version·copy_version·action_config_snapshot 보존 | [PD-192](../../governance/decisions/2026-09-13-track-c-storage-192.md) |
| #310 구현·리뷰 대상 | 위 4개 필드, JSON 객체 CHECK, Barrier별 ACTIVE Plan 최대 1개 | [C1 저장 계약](track-c-storage-v1.md) |
| 별도 제품 결정 기록 | REMINDER_SETUP은 일정 확인·설정으로 연결. occurrence 재알림 POST를 쓰지 않음 | [PD-203 항목 5](../../governance/decisions/2026-09-10-track-b-notifications.md), [일정 HTTP 연결안](track-b-schedule-api-v1.md) |
| 이번 신규 제안 | 파일 기반 설정 보존, JSON 키·schema_version, 지원별 parameters, rationale snapshot, 오류·이행 규칙 | 이 문서. 승인된 계약으로 간주하지 않음 |

특히 REMINDER_SETUP을 “놓친 occurrence 재알림 1회”로 구현하지 않는다. Track B 일반 재알림의
PENDING 조건을 완화하지 않으며, 일정 설정 성공을 놓친 복약의 완료로 표시하지 않는다.

## 3. 설정 정의와 Plan snapshot

### 승인 설정 파일 제안

`backend/app/config/track_c/support-rules/<rule_version>.json`에 한 버전의 6개 지원 정의를 두고,
`backend/app/config/track_c/support-copy/<copy_version>.json`에 한국어 문구와 확인 단계를 둔다.
승인 버전은 Python의 명시적 allowlist에 연결하며 파일 존재만으로 다른 버전을 활성화하지 않는다.
운영 DB에서 수동 수정하는 설정 테이블은 만들지 않고 승인 증빙은 해당 버전의 Decision 문서에서 관리한다.

| 설정 필드 | 타입·검증 제안 | 용도 |
| --- | --- | --- |
| schema_version | 정확히 `track-c-handler-config-v1` | JSON 구조 해석 버전. 제안 문자열이며 확정 필요 |
| rule_version | 1~100자, 빈 문자열 금지 | 해당 파일의 불변 식별자. 실제 버전명은 승인 시 발급 |
| supports | 배열, 6개 support_code 각각 정확히 1개 | 현재 목표의 전체 지원 정의 |
| supports[].support_code | 기존 6개 enum 중 하나 | 분기 식별자 |
| supports[].barrier_codes | 기존 Barrier enum의 중복 없는 배열 | 아래 목표 대응표와 정확히 일치 |
| supports[].priority | bool을 제외한 정수 | 아래 목표 우선순위와 일치 |
| supports[].copy_version | 1~100자, 승인 문구 자료에 존재 | 표시 문구·안내 단계의 불변 버전 |
| supports[].rationale_code | 1~100자, 승인된 사유 목록에 존재 | 해당 지원을 제시한 사유. 실제 코드값은 제품 확인 후 확정 |
| supports[].parameters | support_code별 엄격 객체 | 아래 지원별 허용 설정 |

공통 선행 조건(현재 NOT_TAKEN, 최신 Safety ROUTINE, 현재 Barrier)은 Python Service에서 검증한다.
임의 식·Python 문자열·SQL·조건 DSL을 설정 파일에 넣지 않는다. UNKNOWN/URGENT/EMERGENCY에서는
일반 지원을 반환하지 않는다. ROUTINE+BLOCKED_ACTION 처리 기준은 별도 계약 확인 전 허용을 추론하지 않는다.

### Plan에 저장할 JSON 제안

Plan의 기존 컬럼 `support_code`, `rule_version`, `copy_version`을 정본으로 유지한다.
JSON에는 같은 값을 중복 저장하지 않고 다음 3개 필드를 필수로 둔다.

| JSON 필드 | 타입·검증 제안 |
| --- | --- |
| schema_version | 문자열, 알려진 정확한 버전만 허용 |
| rationale_code | 선택한 승인 지원 정의의 사유 코드와 일치 |
| parameters | 해당 support_code용 엄격 객체. 누락·추가 키 금지 |

다음은 구조 설명용 합성 예시이며 승인된 문구·설정이나 운영 seed가 아니다.

```json
{
  "schema_version": "track-c-handler-config-v1",
  "rationale_code": "SYNTHETIC_REMINDER_REASON",
  "parameters": {
    "destination": "MEDICATION_SCHEDULE_SETUP",
    "prescription_version_medication_id": "00000000-0000-4000-8000-000000000001"
  }
}
```

`prescription_version_medication_id`는 정적 설정 파일에 들어갈 환자별 값이 아니다.
REMINDER_SETUP의 정적 parameters는 destination만 갖고, Plan 생성 시 서버가 소유권을 확인한
Check-in → Occurrence → 처방 약 항목 관계에서 UUID를 얻어 snapshot에 추가한다.
클라이언트가 넘긴 ID로 이 부모 관계를 대체하지 않는다.

## 4. 지원별 구체안

아래 Barrier·priority는 기존 목표이며, **parameters와 화면 동작은 이번 제안**이다.

| support_code | 대상 Barrier / priority | parameters 제안 | 실행·표시 범위 |
| --- | --- | --- | --- |
| REMINDER_SETUP | FORGOT, SCHEDULE_OR_TRAVEL / 10 | destination=`MEDICATION_SCHEDULE_SETUP`; Plan에는 서버가 결속한 prescription_version_medication_id 추가 | 기존 일정 확인·설정 화면 진입. 사용자 확인 후 Track B 일정 PUT 흐름 사용 |
| ROUTINE_OR_TRAVEL_PLAN | SCHEDULE_OR_TRAVEL, FORGOT / 20 | content_key=`ROUTINE_OR_TRAVEL_PLAN` | 해당 copy_version의 승인된 생활·여행 준비 안내. 복용 시간 변경·자동 일정 생성 없음 |
| INSTRUCTION_REVIEW | INSTRUCTIONS_UNCLEAR / 10 | content_key=`INSTRUCTION_REVIEW` | 승인된 복약 지시 확인 안내. 새 복용법 추론 없음 |
| PURPOSE_REVIEW | NEED_DOUBT / 10 | content_key=`PURPOSE_REVIEW` | 승인된 복용 목적 확인 안내. 특정 약 효능을 근거 없이 생성하지 않음 |
| MEDICATION_CONCERN_GUIDANCE | MEDICATION_CONCERN / 10 | content_key=`MEDICATION_CONCERN_GUIDANCE` | ROUTINE 선행 조건 재검증 후 승인 안내. 진단·중단·용량 변경 권고 없음 |
| ACCESS_SUPPORT | ACCESS_OR_COST / 10 | content_key=`ACCESS_SUPPORT` | 승인된 접근·비용 관련 안내. 기관·연락처·URL을 임의 추가하지 않음 |

content_key는 기존 support_code와 같은 값으로 제한하는 제안이다. 추가 자유 문자열이나 임의 URL을
받는 확장 포인트로 쓰지 않는다. 해당 copy_version의 문구 자료 안에서 정확한 항목을 찾아야 한다.
문구 자료에는 지원별 제목·본문·사용자 확인 단계가 필요하지만, 문구 및 단계별 완료 의미는 제품 확인 후 확정한다.
이 문서는 의료 문구 자체나 새 처방·알림 정책을 작성하지 않는다.

초기에는 시간·여행 날짜·자유 메모·약명·용량·증상 원문·Provider payload를 parameters에 넣지 않는 안이다.
사용자별 추가 입력이 필요하다는 제품 결정이 나오면 필드별 필요성과 검증을 추가해 다시 리뷰한다.
최소 안내형 설정이 제품의 “실행계획” 요구를 충족하는지 확인 전 6개 Handler 완료로 선언하지 않는다.

## 5. 생성·조회·실행 책임

1. **설정 로딩:** 버전 중복, 누락 지원, 잘못된 타입/추가 키, 미연결 copy/rationale를 검증한다.
   승인·검증되지 않은 버전은 운영 활성 설정으로 선택하지 않는다.
2. **Support 조회 (#194):** SELF 소유권과 현재 Check-in·Safety·Barrier를 확인하고 조건에 맞는 지원을
   priority ASC, support_code ASC로 최대 2개 제시한다. 반환한 설정은 실행 허가 토큰이 아니다.
3. **Plan 생성 (#194):** 소유권·현재 revision·최신 Safety·Barrier·기존 ACTIVE Plan·선택 지원을 다시 검증한다.
   요청의 support_code와 확정된 버전 식별자로 승인 정의를 찾아 snapshot을 서버에서 조립한다.
   config JSON·rationale·priority를 클라이언트가 임의 덮어쓰지 못하게 한다.
4. **저장 (#192 기반):** 부모부터 기존 잠금 순서를 유지해 Plan과 동기 멱등 응답을 같은 transaction에 저장한다.
   최종 commit 전 오류는 모두 rollback한다. JSON 저장만으로 외부 동작 성공을 표시하지 않는다.
5. **조회:** 과거 Plan에는 저장 당시 rule/copy/config를 사용한다. 현재 설정으로 재구성하지 않는다.
   부모 소유권과 API 노출 조건은 읽기 때도 적용한다.
6. **실행 (#194 및 Track B 연결):** ACTIVE 상태와 최신 안전·처방 조건을 재검증한다.
   REMINDER_SETUP 선택/Plan 저장만으로 일정 PUT을 자동 호출하지 않는다. 설정 화면에서 사용자가
   확인한 Track B 요청의 성공과 Plan 완료 처리는 별도 계약으로 연결해야 한다.
7. **정정·취소:** #193의 non-ROUTINE 정정과 #195의 Check-in 정정은 기존 동일 transaction 취소 계약을 따른다.
   CANCELLED/COMPLETED 이력과 config snapshot을 덮어쓰거나 재활성화하지 않는다.

Track B 일정 PUT과 C Plan 완료가 별도 HTTP 요청이면 원자 완료로 간주하지 않는다.
일정 변경 성공 뒤 Plan 완료가 실패한 경우의 재시도·멱등성·사용자 표시를 #194 인계에서 확정한다.
완료 연결이 없으면 설정 진입만 구현된 상태로 명시하며 자동 COMPLETED 처리를 넣지 않는다.

## 6. 버전·오류·기존 데이터 처리

- JSON 스키마 변경은 schema_version, 지원 대상·우선순위·설정 변경은 rule_version,
  문구 변경은 copy_version으로 구분한다. 이미 발행한 동일 버전 내용을 덮어쓰지 않는다.
- 문구만 바뀌어도 rule 파일 안의 copy_version 참조가 바뀌므로 새 rule_version으로 발행하는 안이다.
- 선택 화면 이후 활성 rule이 바뀌면 새 Plan 생성은 재조회가 필요한 충돌로 처리하는 안이다.
  정확한 HTTP status·오류명·요청 DTO는 #194에서 확정한다. 기존 성공 멱등 재요청은 최초 응답을 재현한다.
- 이전 승인 버전과 문구는 참조 Plan의 보존 정책에 맞춰 함께 보존한다. 최신 버전으로 몰래 대체하지 않는다.
- schema_version 누락·미지원, copy 참조 실패, 부모 ID 불일치, 변조 config는 실행을 거부한다.
  저장 데이터 문제를 일반 NO_ELIGIBLE_SUPPORT(정상 조건상 지원 없음)로 숨기지 않는다.
- 기존 JSON 객체가 `{}`이거나 상세 키가 없어도 임의로 v1·기본값을 backfill하지 않는다.
  기존 행 확인 후 영향 목록과 이행 방식을 별도 검토한다. 조회 시 지원 불가를 어떻게 표시할지도 DTO에서 구분한다.
- 로그·오류에는 원문 snapshot, 환자 식별 정보, 문구 본문을 복제하지 않는다.
- 기존 JSONB와 컬럼을 사용하므로 이 안 자체는 신규 migration이 필요하지 않다.
  API·저장 writer에서 검증을 강제하고 실제 DB round-trip 테스트로 확인한다.

## 7. #192와 후속 완료 기준

| 범위 | 완료 기준 제안 |
| --- | --- |
| #192 HandlerConfig | 이 구체안 승인 기록, 엄격 validator·승인 설정 로딩·snapshot 조립/복원 구현, 기존 데이터 처리 기준 및 DB round-trip 증빙 |
| #193 | 최신 Safety와 Barrier 판단·revision 및 non-ROUTINE 취소 계약 연결 |
| #194 | Support 선택·Plan 생성/완료·실제 화면/일정 연결·follow-up, API/멱등성/오류 계약 및 통합 테스트 |
| #195 | B 정정과 같은 transaction의 C 무효화 |

이 분리는 제안이다. #192의 Handler 실행 완료를 주장하거나 #194 없이 사용자 기능이 끝났다고 표시하지 않는다.
담당자 확인 후 기존 #192 완료 조건과 연결하며, 이 제안 문서만으로 Issue를 닫지 않는다.

## 8. 담당자에게 확인할 구체적인 선택

- **은영님:** 독립 테이블 없이 기존 JSONB 사용, schema_version/rationale/parameters 구조,
  불변 버전 자료 보존과 기존 무버전 JSON 처리, 애플리케이션 검증·동일 transaction 저장 방식.
- **가빈님:** 위 6개 지원의 최소 동작으로 충분한지, 사유 코드·승인 문구/단계의 정본,
  일정 설정 성공과 Plan 완료의 제품 기준. 기존 REMINDER_SETUP 일정 연결 결정을 재질문하지 않고 반영한다.
- **한솔님:** 기존 일정 화면 재사용 및 서버가 확인한 약 항목 연결, 고정 content_key 소비,
  미지원 설정·버전 변경 시 표시. Backend DTO 확정 전에 새 route/enum을 독자 정의하지 않는다.

## 9. #192·#194 전체 검증 계획

1. 6개 코드·Barrier 대응·priority 일치, 누락·중복·미허용 설정 차단.
2. 엄격 타입·필수 키·추가 키·schema_version 검증; bool 정수 변환 금지.
3. REMINDER_SETUP의 부모 약 항목 결속, 타인/다른 처방 ID 주입 거부.
4. REMINDER_SETUP에서 occurrence 재알림 API 및 외부 Provider 호출 0건.
5. 안내형 config에 시간·원문·임의 URL·임의 실행 경로 주입 거부.
6. 설정·문구 버전 변경 후 과거 Plan snapshot과 해석 보존.
7. 미지원/누락 버전·없는 문구의 실행 차단, NO_ELIGIBLE_SUPPORT와 구분.
8. 현재 revision·Safety 변경 후 과거 Support 선택 차단.
9. 동시 Plan 생성에서 ACTIVE 최대 1개 및 동일 멱등 키 재현.
10. snapshot 검증/저장/멱등 응답 실패 시 전체 rollback.
11. CANCELLED/COMPLETED Plan 실행·자동 재활성화 거부.
12. 일정 저장 성공/Plan 완료 실패의 재시도 경계 검증 (#194 연결 시).

위 항목은 전체 #192·#194 인계 테스트 계획이며 일부 저장·복원 항목만 이 변경에서 검증한다.

## 10. 후속 답변과 구현 경계

- 권가빈·남한솔 확인: 여섯 지원은 추가 직접 입력 없는 안내형 최소 동작으로 진행한다.
  `REMINDER_SETUP`만 기존 일정 확인·설정 화면에 연결한다. 화면은 실제 Schedule API를 다시 조회하므로
  Plan snapshot에 약 이름·시간·현재 일정 값을 중복 보존하지 않는다. 일정 설정 성공과 Plan `COMPLETED`의
  관계, 화면 DTO 및 완료 전이는 #194에서 확정한다.
- 지원 사유 코드·안내 문구·사용자 확인 단계는 [PD-192-2](../../governance/decisions/2026-09-15-track-c-handler-config-rules-192.md)로
  제품 승인했다. 실제 Rule·Copy 버전 파일과 활성 allowlist를 추가하고 합성 검증에 운영 파일 회귀 검증을 더한다.
- 이 작업은 기존 JSONB에 엄격한 schema/version/field 검증을 거친 snapshot을 저장·복원하는 내부 경계를
  추가한다. `REMINDER_SETUP` 약 항목 ID는 소유한 Barrier의 부모 관계에서 서버가 얻는다.
  #194가 현재 Check-in·Safety·ACTIVE Plan·멱등성을 같은 transaction에서 검증하기 전에는 공개 Plan 생성
  API에 연결하지 않는다. 기존 `{}` snapshot은 자동 보정하지 않고 복원 거부한다.
- DB 스키마, RLS, DB Trigger, 운영 seed, Frontend와 Provider 호출은 변경하지 않는다.

## 11. 2026-09-15 운영 규칙 제품 승인

[Personalised Adherence Support 상세 v1.3](https://app.notion.com/p/3c0233603e2780c29411d8d271ad60fb)을
제품 참고자료로 사용해 [Rule·한국어 Copy 승인 기록](assets/track-c-handler-config-192/README.md)을 작성했다.
비판단적 표현, 사용자 선택권, Safety 우선과 처방 변경 금지 원칙만 반영했다.

Notion에서 연결된 “구현 상세 설계 v2”는 Draft·Product/Contract 재승인 대기 상태이고 현재 승인 목표보다
확장된 Handler·RAG·LLM·Offer·Follow-up 구조를 포함한다. 따라서 그 확장 범위는 이번 승인 자료와 #192 runtime에
반영하지 않았다. 제품 승인 뒤 불변 Rule·Copy 버전과 활성 allowlist를 운영 로딩 경로에 연결했다.
담당 기술·화면 리뷰와 외부 게이트 전에는 공개 API에 연결하거나 기존 데이터를 backfill하지 않는다.
