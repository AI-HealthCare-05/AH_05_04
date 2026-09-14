# Track B occurrence 원래 약 표시 조회 v1

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed · 리뷰용 구현 완료 · 승인 대기 |
| Decision | [PD-202-HISTORY-20260913](../../governance/decisions/2026-09-13-track-b-occurrence-medication.md) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`), #202 배정 |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend/API·Security; 남한솔 (`solia142`) — Frontend 소비, #202 배정 |
| 관련 범위 | #202 Backend 인계, #421 알림 연결, #138 복약 기록 |

이 문서는 새 조회 경로의 승인 요청안이다. 현재 API 목록이나 Frontend production 계약으로
사용하지 않는다. 이 브랜치의 구현과 함께 검토한다.

## 문제와 조회 범위

과거 occurrence는 당시 prescription version medication에 귀속된다. 현재 처방 상세/latest는
활성 version의 medications를 반환하므로 약명·배열 순서로 서로 연결할 수 없다.
원래 occurrence 한 건에서 당시 확정 약 표시 정보 한 건을 조회하는 읽기 전용 경로를 제안한다.

```http
GET /api/v1/medication-occurrences/{occurrence_id}/medication
```

- Bearer 인증 필수. path는 UUID. body, 별도 query filter, Idempotency-Key는 정의하지 않는다.
- occurrence의 `PENDING`, `CLOSED`, `CANCELLED` 여부와 Check-in 유무를 조회 제한에 쓰지 않는다.
  비활성 version과 종료·취소 schedule에 보존된 occurrence도 같은 SELF 소유권으로 읽는다.
- 약 snapshot은 occurrence → schedule → prescription version medication에서 찾는다.
  최신 version, 현재 schedule time revision, 알림 scheduled_at, 이름·배열 순서로 대체하지 않는다.
- 날짜·예정 시각·상태·Check-in은 기존 날짜별 occurrence 응답에서 소비한다.
  이 API는 복용 가능 여부나 현재 권장 처방을 판정하지 않는다.

## 성공 응답 후보

HTTP 200, `{"data": {...}}`. 아래 모든 키는 응답에 존재하며 nullable 값은 JSON null로 반환한다.

| data 필드 | JSON 타입 | nullable | 근거/의미 |
| --- | --- | --- | --- |
| occurrence_id | UUID string | 아니오 | 요청 occurrence ID |
| prescription_version_id | UUID string | 아니오 | 원래 약 snapshot의 version ID |
| prescription_version_medication_id | UUID string | 아니오 | 원래 약 snapshot ID |
| medication_name | string | 아니오 | 당시 사용자 확정 약명 |
| strength_text | string | 예 | 당시 제품 함량, 1회 복용량과 구별 |
| dose_value | number | 예 | 당시 1회 복용량, 기존 MedicationData의 숫자 표현 재사용 |
| dose_unit | string | 예 | 당시 1회 복용량 단위 |

합성 응답 예시이며 실행된 fixture가 아니다.

```json
{
  "data": {
    "occurrence_id": "11111111-1111-4111-8111-111111111111",
    "prescription_version_id": "22222222-2222-4222-8222-222222222222",
    "prescription_version_medication_id": "33333333-3333-4333-8333-333333333333",
    "medication_name": "합성시험약 A",
    "strength_text": null,
    "dose_value": 1.0,
    "dose_unit": "정"
  }
}
```

최소 표시 범위로 약명·함량·1회 복용량을 제안한다. frequency_per_day, timing_text,
duration_days까지 #138 화면에서 필요한지는 소비 리뷰에서 확인한다. 이 값이나 기타 필드를
추가하기로 결정하면 이 Proposed 문서에서 먼저 정렬한다. 원문 OCR·환자 정보·document_id,
다른 약 목록·공식 Identity·Provider 결과는 반환하지 않는다.

## 소유권·오류·읽기 불변성

SELF chain은 occurrence → schedule → prescription version medication → prescription version
→ prescription → profile(SELF, 요청 user)이다. DB 조회 단계에서 이 chain을 확인하고,
소유권을 통과하기 전 의료 표시 정보를 직렬화하지 않는다. active version 필터는 사용하지 않는다.

| 상황 | HTTP | code 및 의미 |
| --- | --- | --- |
| 인증 실패 | 401 | 기존 인증 오류 계약 재사용 |
| UUID 형식 오류 | 422 | VALIDATION_FAILED, 기존 공통 validation 처리 |
| 없는 occurrence 또는 SELF 소유가 아님 | 404 | MEDICATION_OCCURRENCE_NOT_FOUND |
| 필요한 부모 chain 조회 실패 | 404 | 같은 MEDICATION_OCCURRENCE_NOT_FOUND; 대체 약 조회 없음 |
| 예상하지 못한 서버 실패 | 500 | 기존 공통 서버 오류 처리, 예외 원문 비노출 |

404는 기존 Check-in의 `message="복약 일정을 찾을 수 없습니다."`, `details=[]`를 재사용한다.
요청별 trace_id 외에 없는 ID와 타인 ID를 구별하는 정보는 제공하지 않는다.
오류는 [공통 envelope](../current/backend-error-response.md)를 사용한다.
성공·실패에 기존 `Cache-Control: no-store`, `X-Trace-Id`를 유지한다.

GET은 알림 read_at, occurrence·schedule 상태, Check-in·Audit·멱등성 row를 변경하지 않는다.
migration·신규 DB 권한·Provider 호출·새 enum·새 오류 code·쓰기 transaction 변경은 필요하지 않다.

## Frontend 인계 후보

1. 알림의 occurrence_local_date로 기존 날짜별 API를 조회하고 occurrence_id로 기록을 찾는다.
2. 같은 occurrence_id로 이 GET을 호출한다. 응답의 occurrence ID·version ID·약 ID가
   날짜별 응답과 일치할 때만 약 표시 정보를 연결한다.
3. 다른 약 화면으로 이동한 뒤 도착한 응답은 원래 요청 ID에만 연결한다.
4. 조회 실패·ID 불일치 시 최신 처방 약으로 보충하지 않고 표시 실패·재시도 상태를 제공한다.
   확정 문구·포커스·모바일 렌더링은 #421/#138의 Frontend 인수에서 정한다.
5. 읽음과 복약 저장을 구분한다. Check-in은 기존 PUT의 명시적 사용자 의도·revision·멱등 키로만
   저장하며, 취소 occurrence와 revision 충돌 처리도 기존 계약을 유지한다.

## 구현·검증 조건

아래 항목을 구현·검증 기준으로 사용한다. 실제 실행 결과와 남은 항목은 [검증 기록](../../validation/track-b/issue-202-closure-readiness.md)에 구분한다.

- 실제 v1 등록·OpenAPI에서 URL·인증·response requiredness/nullability·오류 envelope 확인.
- 현재/과거 version, PENDING/CLOSED/CANCELLED, Check-in 없음/TAKEN/NOT_TAKEN/UNCONFIRMED 조회.
- 실제 처방 정정 전후 같은 occurrence의 약명·함량·용량·식별자가 불변이고 latest는 새 약을 반환.
- 같은 이름·display_order로 만든 다른 version 약이 대체되지 않음. nullable 필드 추정 없음.
- 다른 사용자 ID와 없는 ID는 동일 404, 무인증 401·잘못된 UUID 422·no-store·trace 확인.
- GET 반복·재시도·오류에서 알림 읽음·Check-in·Audit·일정·occurrence 상태 변경 0건.
- #468과 같은 기존 합성 seed·알림 경로를 재사용해 별도 HTTP fixture로 원래 날짜·세 ID 정합성 검증.
- 필수 Ruff/Mypy/관련 계약·DB 통합 테스트/전체 runner와 책임 리뷰 결과를 구현 PR에 기록.

새 경로·DTO·문서·계약/통합 테스트는 승인된 동일 기준으로 구현한다. 필요한 승인·구현·실행 증빙이
갖춰지기 전 Proposed를 Current로 옮기지 않는다. #202 종료 판정은
[종료 점검표](../../validation/track-b/issue-202-closure-readiness.md)를 따른다.
