# Track B UNCONFIRMED backlog v1

- 문서 상태: Proposed · 리뷰 대기
- 구현 상태: 조회 코드·테스트 후보 구현, 공개 router 등록 보류
- Decision: [PD-418](../../governance/decisions/2026-09-10-unconfirmed-backlog-418.md)
- 기존 목표: [Check-in v1](../targets/post-mvp-1/checkin-v1.md)
- 구현 담당 권가빈; 책임 리뷰 송은영(Backend), 남한솔(Frontend)

## 후보 요청

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

성공·빈 목록·오류 모두 공통 middleware의 `Cache-Control: no-store`를 따른다. 새 공개 예외 코드 `CHECKIN_CURSOR_NOT_FOUND`는 본 제안 승인 대상이다.

## 보완 연결과 동시성

선택 항목의 `occurrence_id`, `revision`을 #202 Check-in PUT의 대상과 `expected_revision`에 사용한다. TAKEN/NOT_TAKEN 보완 후 해당 기록은 재조회 목록에서 제외된다. PUT 오류·멱등성·Audit·C invalidation은 기존 구현을 재사용한다. 이전 revision으로 보완하면 기존 PUT의 409 규칙을 적용하고 목록을 재조회한다. GET은 보완과 직렬화하지 않으므로 응답 직후 revision이 달라질 수 있다.

전체 traversal은 snapshot이 아니다. 도중 추가된 cursor 앞의 기록은 최초 페이지 재조회로 확인한다. 여러 건 보완은 항목별 PUT이며 원자적 batch 성공을 보장하지 않는다. #138 Frontend 연결은 남한솔 담당이다.

## 검증과 남은 승인

합성 PostgreSQL 테스트에서 historical snapshot, 동일시각 tie-break, corrected cursor, 보완 제외와 revision 충돌, SELF ownership, 조회 무변경 및 후보 HTTP 인증/검증/오류/no-store를 검증한다. 실제 서비스 OpenAPI에는 route가 없음을 검사한다.

책임 리뷰 승인 후 router 등록, #202가 병합된 코드의 실제 PUT-GET HTTP 통합 검증, 문서 상태/index 전환이 필요하다. 현재 문서는 실행 중인 공개 계약이나 승인된 target을 대체하지 않는다.
