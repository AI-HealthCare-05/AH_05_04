# Track B UNCONFIRMED backlog v1

- 문서 상태: Proposed · #462 등록 구현 병합·승인 확인, Current 승격은 별도 검토
- 구현 상태: #426 후보 구현에 이어 #462가 2026-09-13 실제 v1 등록·보완·페이지 이동·날짜별 revision 일치 검증을 포함해 병합됐다.
- Decision: [PD-418](../../governance/decisions/2026-09-10-unconfirmed-backlog-418.md)
- 기존 목표: [Check-in v1](../targets/post-mvp-1/checkin-v1.md)
- 구현 담당: 권가빈 (`hazelnutflavoured`). 상태·참조 정리 책임 리뷰어: 남한솔 (`solia142`) — Backend/API 등록 근거와 Frontend 소비 계약의 문서 정합성. 과거 영역별 승인 범위는 PD-418 참조.

## 등록된 요청

`GET /api/v1/medication-checkins/unconfirmed`

Bearer 인증 필요. `limit`은 정수 1..100, 생략 시 20. `cursor`는 선택 UUID이며 직전 응답의 `next_cursor`를 전달한다. 사용자·처방·상태·기간 필터는 정의하지 않는다. SELF parent chain: Checkin → Occurrence → Schedule → PrescriptionVersionMedication → PrescriptionVersion → Prescription → Profile(SELF, 요청 user).

UNCONFIRMED만 `scheduled_at ASC, checkin_id ASC`로 반환한다. 비활성 과거 version과 종료 schedule에 보존된 기록도 포함한다. 날짜 cutoff는 없다. Cursor는 동일 소유 chain으로 조회하고 현재 status와 관계없이 저장된 정렬 키로 복원한다. Check-in과 예정 시각이 보존되는 동안 앞 페이지를 보완해도 다음 페이지를 건너뛰지 않는다.

## 200 응답

`data.items`는 아래 필드의 배열이다. `data.next_cursor`는 후속 항목이 있을 때 마지막 반환 Check-in UUID, 없으면 null이다. 빈 응답은 `{"data":{"items":[],"next_cursor":null}}`이다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| checkin_id | UUID | 현재 Check-in ID, cursor anchor |
| occurrence_id | UUID | #202 PUT 보완 대상 |
| prescription_id | UUID | 원본 처방 |
| prescription_version_id | UUID | 해당 일정의 과거/현재 불변 버전 |
| prescription_version_medication_id | UUID | 해당 버전의 약품 행 |
| medication_name | string | 확정 snapshot의 약 이름 |
| strength_text | string 또는 null | 확정 snapshot의 함량 원문 |
| scheduled_local_date | YYYY-MM-DD | 저장된 복약 예정 로컬 날짜 |
| scheduled_at | timezone 포함 datetime | 저장된 예정 instant |
| confirmation_deadline_at | timezone 포함 datetime | 저장된 확인 기한 |
| status | literal UNCONFIRMED | 현재 결과 |
| revision | integer >= 1 | 보완 optimistic concurrency에 사용할 현재 revision |

모든 필드는 항상 존재한다. Check-in Audit, 사용자 식별자, OCR 원문, 문서 저장 경로, 의료 권고는 포함하지 않는다. 로그인·조회·“나중에”는 mutation을 하지 않는다.

## 오류·캐시

공통 `code/message/details/trace_id` envelope를 따른다.

| 상태 | code | 조건 |
| --- | --- | --- |
| 401 | UNAUTHORIZED | 인증 정보 없음 |
| 401 | INVALID_TOKEN / EXPIRED_TOKEN | 잘못되거나 만료된 token: 기존 인증 의존성 계약 |
| 404 | CHECKIN_CURSOR_NOT_FOUND | cursor 미존재 또는 SELF 소유권 불일치, 구별하지 않음 |
| 422 | VALIDATION_FAILED | 잘못된 UUID 또는 limit 형식/범위 |

성공·빈 목록·오류 모두 공통 middleware의 `Cache-Control: no-store`를 따른다. `CHECKIN_CURSOR_NOT_FOUND`는 #462에 등록된 오류 코드다.

### Cursor 404의 Frontend 복구

`404 CHECKIN_CURSOR_NOT_FOUND`는 조회 위치를 복원할 수 없다는 뜻이며, 빈 목록이나 마지막 페이지를 의미하지 않는다. Backend는 이 오류를 그대로 반환하며 첫 페이지로 자동 fallback하지 않는다. Frontend는 다음 순서로 복구한다.

1. 저장된 cursor와 페이지 누적 상태를 초기화하고 `cursor=null` 상태로 돌아간다.
2. 같은 `limit`으로 `cursor` query parameter를 **생략**해 첫 페이지를 재조회한다. `cursor=null` 문자열을 전송하지 않는다.
3. 성공한 응답의 `items`로 목록을 교체하고 새 `next_cursor`로 이어 조회한다. 기존 페이지에 append하지 않는다.
4. 재조회가 실패하면 오류·재시도 상태를 표시한다. 404만으로 빈 목록·보완 완료를 표시하지 않는다. 빈 목록은 성공한 `200`의 `items=[]`로, 후속 페이지 없음은 성공한 `200`의 `next_cursor=null`로 판단한다.

이 복구는 미존재·삭제·SELF 소유권 불일치 cursor를 구분하지 않고 동일하게 적용한다. Frontend 복구 구현과 소비 검증은 #138 연결 시 확인할 항목이며, 현재 Backend 테스트가 Frontend 복구 완료를 증명하지는 않는다.

## 보완 연결과 동시성

선택 항목의 `occurrence_id`, `revision`을 #202 Check-in PUT의 대상과 `expected_revision`에 사용한다. TAKEN/NOT_TAKEN 보완 후 해당 기록은 재조회 목록에서 제외된다. PUT 오류·멱등성·Audit·C invalidation은 기존 구현을 재사용한다. 이전 revision으로 보완하면 기존 PUT의 409 규칙을 적용하고 목록을 재조회한다. GET은 보완과 직렬화하지 않으므로 응답 직후 revision이 달라질 수 있다.

전체 traversal은 snapshot이 아니다. 도중 추가된 cursor 앞의 기록은 최초 페이지 재조회로 확인한다. 여러 건 보완은 항목별 PUT이며 원자적 batch 성공을 보장하지 않는다. #138 Frontend 연결은 남한솔 담당이다.

## 검증과 문서 상태

합성 PostgreSQL과 실제 ASGI 앱으로 historical snapshot, 동일시각 tie-break, corrected cursor,
보완 제외·revision 충돌, SELF ownership, 조회 무변경, HTTP 인증·검증·오류·no-store를 검증한다.
실제 v1 route가 한 번만 등록되고 OpenAPI의 요청·응답·오류가 DTO와 일치하는지 확인한다.
기존 PUT의 TAKEN/NOT_TAKEN 보완, 동일 키 replay, stale revision 409, cursor 404 복구와
다건 보완 중 페이지 이동을 검증한다. #456 날짜별 조회의 Check-in은 PUT 응답과 같은
revision·status를 반환해야 한다. Frontend가 사용할 합성 fixture도 DTO로 검증한다.

#426의 승인은 미등록 후보에 한정된다. #462는 등록 구현에 대한 남한솔의 APPROVED와
최종 HEAD CI 7/7 성공 후 병합됐다. 정확한 commit·승인 범위는
[PD-418 승인 증빙](../../governance/decisions/2026-09-10-unconfirmed-backlog-418.md#승인-증빙과-등록-변경의-병합-조건),
실행 결과는 [검증 기록](../../validation/issue-418-unconfirmed-backlog.md)을 따른다.

등록 구현의 병합과 계약 문서의 Current 승격을 구분한다. #462는 문서 이동 없이 병합됐으므로
이 문서는 Proposed에 유지한다. 문서 정리만으로 구현 PR 내 승격 요건을 충족했다고 간주하지
않으며, 후속 승격은 [문서 권위·승격 규칙](../../governance/post-mvp-1-document-authority.md#충돌과-승격-규칙)에
따라 별도 검토한다. 이전 두 리뷰어 재승인 대기를 #418 종료 blocker로 반복하지 않는다.
#418의 남은 종료 조건은 이 상태·Decision·인덱스 정리의 승인·병합과 이슈 체크리스트 갱신이다.
#138의 Frontend 화면·실제 브라우저 E2E와 Production 공개 승인은 별도다.
