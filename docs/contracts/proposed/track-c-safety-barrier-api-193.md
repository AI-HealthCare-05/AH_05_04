# Track C Safety·Barrier API v1 — #193

- 상태: Proposed / 구현 브랜치 검증 대상. 전체 Safety 정책 완료나 Current 승격 아님.
- 구현 소유자: 권가빈 @hazelnutflavoured. 책임 리뷰어: @phina-io (Backend·Transaction·Security).
- 소비 계약 의견: @solia142 (#139). 제품 수용: @hazelnutflavoured.
- 근거: [Check-in target](../targets/post-mvp-1/checkin-v1.md),
  [PD-193](../../governance/decisions/2026-09-15-track-c-safety-barrier-api-193.md).

## HTTP 구체화

두 API는 인증 및 `Idempotency-Key` 필수, 성공 `200` + `data` envelope다.
오류는 공통 `code/message/details/trace_id`, 모든 응답은 `Cache-Control: no-store`다.
추가 body 필드는 거부한다. revision은 엄격한 정수이며 Check-in revision은 양수,
expected revision은 0 이상이다.

| API | 요청 | data 필드 |
|---|---|---|
| `POST /api/v1/safety-assessments` | `medication_checkin_id`, `checkin_revision`, `symptom_codes`, `expected_revision` | `assessment_id`, `medication_checkin_id`, `checkin_revision`, `response_level`, `safety_disposition`, `message_code`, `copy_version`, `source_version`, `revision` |
| `PUT /api/v1/medication-checkins/{checkin_id}/barrier-response` | `response_status`, nullable `barrier_code` (생략 시 null), `checkin_revision`, `expected_revision` | `barrier_response_id`, `medication_checkin_id`, `checkin_revision`, `safety_assessment_id`, `response_status`, `barrier_code`, `revision` |

Safety code 형식은 `[A-Z][A-Z0-9_]{0,63}`, 목록 최대 32개다. 임상 allowlist가 아니라
자유 문장·무제한 payload 저장을 방지하는 입력 형식이다. 문자열 또는 목록 내 비코드 문자열은
`422 FREE_TEXT_SYMPTOM_NOT_SUPPORTED`, 다른 DTO 위반은 `422 VALIDATION_FAILED`다.
배열 순서·중복을 조용히 수정하지 않고 요청 fingerprint에 그대로 반영한다.

Barrier enum은 기존 6개 그대로다. ANSWERED는 code 필수, DECLINED는 null만 허용한다.
미제출은 row가 없다. 최초 expected revision=0, 정정은 해당 Check-in revision의
최신 Barrier revision을 제출한다. 불일치는 `409 BARRIER_RESPONSE_REVISION_CONFLICT`로 구체화한다.
Check-in 상태·revision 불일치의 `CHECKIN_FLOW_STALE`과 code로 구분한다.
소비자는 Barrier 충돌에서 선택 입력을 보존하고 최신 Barrier revision을 확인해야 한다.
전용 Barrier GET은 현재 이 PR에 없으므로 존재하지 않는 조회 경로를 호출하도록 구현하지 않는다.

## 상태·트랜잭션

1. SELF 부모 chain 소유권을 먼저 확인한다. 미존재·타인 모두 `404 MEDICATION_CHECKIN_NOT_FOUND`.
2. 같은 키·같은 fingerprint는 현재 상태 검사보다 먼저 최초 성공 snapshot을 재현한다.
   같은 키·다른 fingerprint는 `409 IDEMPOTENCY_KEY_CONFLICT`다.
3. 신규 mutation은 Check-in을 잠그고 현재 NOT_TAKEN과 요청 checkin_revision을 검사한다.
   불일치하면 `409 CHECKIN_FLOW_STALE`이다.
4. Safety는 같은 checkin_revision의 최신 revision 검사 후 append-only 저장한다.
   불일치 `409 SAFETY_ASSESSMENT_REVISION_CONFLICT`.
5. Barrier는 같은 checkin_revision의 최신 Safety가 ROUTINE일 때만 저장한다.
   없거나 non-ROUTINE이면 `409 SAFETY_FLOW_PRECEDES_BARRIER`.
6. non-ROUTINE Safety 정정은 같은 transaction에서 기존 ACTIVE Plan을 CANCELLED로 변경한다.
   Safety·Barrier 이력은 유지한다. 잠금 순서는 Check-in → Safety → Barrier → Plan이다.
7. mutation과 암호화 snapshot은 같은 transaction이다. 실패·snapshot cap 초과는 전체 rollback한다.

Check-in revision마다 Safety·Barrier revision을 새로 1부터 시작한다. 이전 revision의
Safety로 새 Barrier를 만들 수 없다. #195의 Track B 정정 adapter 연결은 이 PR에 포함하지 않는다.

## Safety 정책의 구현 한계

빈 목록만 `ROUTINE/NORMAL`, 비어 있지 않은 코드 목록은 `UNKNOWN/UNKNOWN_RISK`다.
이는 synthetic 기술 통합용 foundation이며 임상 triage 구현 완료가 아니다.
`NO_SYMPTOMS_CONFIRMED` / `SYMPTOM_POLICY_APPROVAL_REQUIRED`와
`copy_version=track-c-safety-foundation-v1`, `source_version=contract-freeze-v4`는
foundation 식별자이고 NHS 또는 의료 검토 완료를 뜻하지 않는다. 환자용 문구 catalog는 아직 없다.
URGENT/EMERGENCY 상태를 받은 이후의 차단은 합성 fixture로 검증하지만 실제 증상 판정은 미구현이다.

사용자가 제시한 NHS는 후속 정책의 참고 출처다. [아나필락시스 안내](https://www.nhs.uk/conditions/anaphylaxis/)는
급격한 기도 부종·호흡 곤란 등에서 즉각 응급 도움을 요청하도록 안내한다.
사이트 URL만으로 앱 symptom enum·한국어 문구·우선순위·국내 연락 경로가 확정되지는 않는다.
NHS의 영국 999/111 번호를 한국어 앱에 그대로 복사하지 않는다.

### NHS 기반 판정표 초안 — 아직 런타임에 적용하지 않음

증상 선택표·판정 우선순위·한국어 안내·출처·검증 사례의 정본은
[Safety 정책·문구 검토안](track-c-safety-policy-copy-193.md)으로 모았다.
이전 4개 예시 표를 중복 유지하지 않는다. 최근 24시간 신경학적 신호 및 증상 없음 확인의
의미 확장도 명시적인 검토 항목이며, 현재 API는 제안 코드를 보내더라도 UNKNOWN을 반환한다.

## #139 인계

Backend가 반환한 response_level만 사용하고 Frontend에서 긴급도를 재판정하지 않는다.
ROUTINE만 Barrier 진행, 나머지는 일반 지원 진행 차단이다.
API 합성 fixture는 `backend/app/tests/track_c/test_track_c_api.py`에 있다.
Support 조회·선택·Plan 완료는 #194, Check-in 정정 무효화 연결은 #195다.

## PD-193 revision 3 — 내부 Local 7일 합성 데모

위 foundation은 기본 동작이다. 사용자 요청에 따른 opt-in 구현은
[PD-193 revision 3](../../governance/decisions/2026-09-16-track-c-internal-demo-193.md)을 따른다.
의료 검토나 외부 승인을 주장하지 않으며 이 문서의 Proposed 상태를 유지한다.

| 설정 | 기본값 / 조건 |
| --- | --- |
| `TRACK_C_SAFETY_DEMO_ENABLED` | `false`; `ENV=local`만 허용 |
| `TRACK_C_SAFETY_DEMO_STARTS_AT` | null; 활성 시 timezone-aware 시각 필수 |
| `TRACK_C_SAFETY_DEMO_EXPIRES_AT` | null; 시작보다 크고 최대 7일 이내 |
| `TRACK_C_SAFETY_DEMO_USER_IDS` | 빈 목록; 활성 시 합성 계정 UUID 목록 필수 |

환경·기간 형식·계정 목록은 기동 시 검증한다. 신규 Safety mutation 시 다시
`start <= now < expiry`, Local, 사용자 allowlist 및 artifact SHA-256을 검사한다.
잘못된 설정으로 기동하거나 만료 후 서버 재시작으로 기간이 자동 연장되지 않는다.
합성 여부를 자유 텍스트에서 자동 판별하지 않는다. 운영자는 격리된 합성 DB·계정을 사용한다.

| 입력 | 결과 | message_code |
| --- | --- | --- |
| `[]` | 기존 `ROUTINE/NORMAL`과 foundation 버전 유지 | `NO_SYMPTOMS_CONFIRMED` |
| 정책 검토안 E01~E11 중 하나 이상 | `EMERGENCY/EMERGENCY_ROUTED` | `DEMO_SAFETY_EMERGENCY_HELP` |
| 응급 없이 U01 | `URGENT/URGENT_ROUTED` | `DEMO_SAFETY_URGENT_CARE` |
| 그 외 모든 non-empty | `UNKNOWN/UNKNOWN_RISK` | `DEMO_SAFETY_UNCERTAIN` |

응급 + 미등록/불확실 및 긴급 + 미등록/불확실 입력도 각각 응급/긴급 안내를 유지한다.
모두 일반 지원을 차단한다. 순서·중복은 판정에 영향을 주지 않지만 기존 fingerprint에는 그대로 남긴다.
형식 위반은 기존 422다. 새 증상이나 시간 조건을 서버가 추론하지 않는다.

non-empty 결과의 `copy_version=track-c-safety-demo-ko-2026-09-16.1`,
`source_version=track-c-safety-demo-source-2026-09-16.1`은
`rule_version=track-c-safety-demo-rule-2026-09-16.1`과 일대일로 결속된다.
정본은 [데모 artifact](../../../backend/app/config/track_c/safety-demo/track-c-safety-demo-2026-09-16.1.json)다.
SHA-256 `ba2579c1299ba800fca2d23c450a7d9abaf2f47bcec5490eb8c61ec0c9a7893b`를 코드에서 검사한다.
파일 누락·변조·버전/문구/출처 변경을 묵인하지 않는다.

`503 SAFETY_DEMO_UNAVAILABLE`는 새로운 mutation의 환경·기간·계정·artifact 실패다.
Safety/Plan/멱등 snapshot을 새로 저장하지 않는다. 기존 성공 replay는 소유권 확인 후
최초 snapshot을 재현한다. Barrier 오류·취소 transaction·소유권 404는 바뀌지 않는다.

이 API 응답에는 문구 본문이나 선택 목록 필드를 추가하지 않는다. 기존 화면은 증상 없음 경로만
호출하므로 이번 Backend 구현만으로 증상 선택 UI까지 연결됐다고 보지 않는다.
소비자는 artifact의 `notice`, 선택 조건, copy/source 쌍을 함께 확인해야 하며,
알 수 없는 버전·저장 실패를 정상 판정으로 표시하면 안 된다. Frontend 변경은 별도 작업이다.
