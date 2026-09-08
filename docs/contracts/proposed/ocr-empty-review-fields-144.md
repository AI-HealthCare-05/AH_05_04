# #144 빈 검수 필드 생성 변경안

- 상태: **Proposed · Partially implemented (작업 브랜치)** (2026-09-08)
- 관련 이슈: [#144](https://github.com/AI-HealthCare-05/AH_05_04/issues/144)
- 구현 담당: 김지혜 (`Jye-rookie`)
- 주 리뷰: 송은영 (`phina-io`) — API·DB, 추가 확인: 남한솔 (`solia142`) — DOC-03
- 근거: [범위 협의 댓글](https://github.com/AI-HealthCare-05/AH_05_04/issues/144#issuecomment-5489219395), [Decision 초안](../../governance/decisions/2026-09-08-ocr-empty-review-fields-144.md)
- 대조 기준: `7d4510f5f6e1f9f70fd25ce4bff30127fda41955`

이 문서는 [현재 OCR 구조화 계약](../current/ocr-medication-structuring.md)에
추가할 변경분만 정의한다. 전체 계약을 복제하거나 현재 실행 동작을 선언하지 않는다.

## 1. 생성 대상

기존 구조화가 약품명을 식별한 `medication_index > 0`의 약품 행마다 아래
6개 필드가 유효한 인식값 또는 빈 검수 필드로 존재하도록 한다.
이 집합은 이슈의 두 경로 기준 통일 및 규칙 경로 DOSE_VALUE·DOSE_UNIT
동시 누락 테스트 요구를 기존 LLM 보충 대상에 적용한 구현 제안이다.

| 필드 | 현재 LLM 누락/grounding 실패 | 현재 규칙 인식 실패 | 제안: 두 경로 | 기존 null 확정 |
| --- | --- | --- | --- | --- |
| MEDICATION_STRENGTH | 생략 | 생략 | 빈 필드 | 허용 |
| DOSE_VALUE | 빈 필드 | 생략 | 빈 필드 | 거부 |
| DOSE_UNIT | 생략 | 생략 | 빈 필드 | 허용 |
| FREQUENCY_PER_DAY | 빈 필드 | 생략 | 빈 필드 | 거부 |
| DURATION_DAYS | 빈 필드 | 생략 | 빈 필드 | 거부 |
| TIMING | 빈 필드 | 생략 | 빈 필드 | 허용 |

`MEDICATION_NAME`은 보충 대상이 아니다. LLM 약품명 검증 실패는 기존 실패
정책을 유지한다. 규칙 경로에서는 기존 약품 행 탐지 및 약품명 생성에 성공한
행에만 보충하며, 헤더·안내문·미확인 token으로 행을 새로 만들지 않는다.

`PRESCRIBED_DATE`는 index 0의 문서 필드이므로 이 집합에서 제외한다.
LLM의 기존 빈 처방일 처리와 규칙 경로의 날짜 탐지 정책을 유지한다.
두 경로 기준 통일은 이 문서의 6개 약품 필드에 적용한다.

## 2. 값 보존·중복·순서

1. LLM은 값 누락과 grounding 실패 두 분기 모두 같은 보충 대상을 적용한다.
   실패한 LLM 값과 그 metadata를 빈 필드에 복사하지 않는다.
2. 규칙 경로는 기존 구조화 결과에서 누락된 유형만 보충한다.
   dose 인식에 실패하면 DOSE_VALUE와 DOSE_UNIT을 함께 보충한다.
3. 유효하게 인식된 필드의 raw/normalized/version/confidence를 그대로 보존한다.
   빈 값으로 덮어쓰거나 유효값을 재정규화하지 않는다.
4. 보충은 `(medication_index, field_type)`의 존재 여부로 판단하며 같은 대상에
   반복 적용해도 행을 늘리지 않는다. 기존 중복 입력을 임의 병합하는 기능은 추가하지 않는다.
5. 기존 필드의 상대 순서를 유지한다. 보충을 순회할 때는 위 표의 순서를 사용하고
   set 순회에 의존하지 않는다. API 소비자는 배열 위치 대신 field_type/index/field_id를 사용한다.

## 3. 빈 필드와 사용자 확인

보충 시 `raw_value`, `normalized_value`, `normalization_version`,
`confidence_score`는 모두 null이다. 0, 빈 문자열, 추정값으로 대체하지 않는다.
DB 저장 시 `confirmation_status=UNCONFIRMED`, `confirmed_value=null`,
`confirmed_at=null`로 시작한다. Provider DTO에 확인 상태 필드를 새로 추가하지 않는다.

저장 후 기존 조회 응답이 DB에서 생성한 `field_id`를 반환하고,
`PATCH /api/v1/extracted-fields/{field_id}`로 사용자가 값을 입력한다.
PATCH는 원문·정규화값을 변경하지 않고 기존 사용자 확인 동작을 유지한다.

Optional의 null 확정은 표에 표시한 세 유형에만 허용한다. 성공 시 기존대로
CONFIRMED 및 확인 시각을 기록한다. 용량 값·횟수·기간의 빈 필드 생성은
그 값의 null 확정을 허용한다는 뜻이 아니다. 기존 소유권·Job 상태·처방 확정 후
수정 차단·필수값 검증을 유지하며 미확인 OCR 값을 확정 처방에 사용하지 않는다.

## 4. 저장·기존 데이터 경계

- 기존 `uq_extracted_field_identity`와 null/confirmation CHECK를 유지한다.
  이슈에 적힌 과거 CHECK 명칭 대신 실제 migration에서 확인한 제약을 검증한다.
- Backend·Worker의 기존 결과 저장 transaction에서 빈 필드도 함께 저장한다.
  저장 실패 시 일부 결과만 남기지 않는 기존 경계를 검증한다.
- 별도 API, field_id 없는 upsert, 신규 DB 컬럼은 계획하지 않는다.
  실제 PostgreSQL 제약 검증에서 불일치가 발견되면 근거와 함께 조정한다.
- 적용 후 새로 구조화하는 결과가 대상이다. 기존 저장 결과의 자동 backfill,
  이미 검수·확정한 처방 변경 또는 OCR 강제 재실행은 포함하지 않는다.
- 공개 route·DTO 형식은 유지하되 응답 필드의 존재 여부는 바뀐다.
  DOC-03에서 기존 field_id 기반 입력·null 확인이 가능한지 소비 확인을 받는다.

## 5. 구현 인수 테스트

| ID | 입력·상황 | 기대 결과 |
| --- | --- | --- |
| ER-01 | LLM strength_text/dose_unit 각각 null | 해당 빈 필드, 네 metadata null |
| ER-02 | 함량/단위 각각 근거 없는 값 또는 다른 행 근거 | 추정값 미저장, 해당 빈 필드; 약품명 실패는 기존 정책 |
| ER-03 | 규칙 경로 dose 인식 실패 | 같은 약품 행에 DOSE_VALUE·DOSE_UNIT 빈 필드 |
| ER-04 | 양 경로에서 약품명만 유효 | 기존 약품 행 유지, 6개 보충 필드, 새 약품 행 없음 |
| ER-05 | 정상 인식과 누락이 섞인 여러 약품 행 | 정상값 보존, index별 보충, identity 중복 없음 |
| ER-06 | 규칙 입력에 약품 행 없음/헤더 없음/안내문만 존재 | 보충 약품 행 없음, 기존 날짜 동작 유지 |
| ER-07 | 동일 보충 처리 반복 | 구성원·값·순서 동일, duplicate 없음 |
| ER-08 | 빈 함량·단위 저장→조회→값 PATCH | 실제 field_id로 성공, 원문·정규화 metadata는 null 유지 |
| ER-09 | 빈 함량·단위·TIMING 각각 null PATCH | CONFIRMED·확인 시각 저장; 필수 필드 null은 기존 거부 |
| ER-10 | 소유자 아님/처방 확정 후 PATCH | 기존 접근·상태 오류 유지 |
| ER-11 | Worker 저장 및 DB 제약 | null 보존, identity unique, 실패 시 부분 저장 방지 |
| ER-12 | DOC-03에서 미인식 Optional 편집 | 기존 UI 입력·저장 및 optional null 확인 가능 |

3단계 구조화 단위 회귀는 [실행 증빙](../../testing/optional-review-fields-144.md)에 기록했다.
DB·PATCH 통합 검증도 같은 증빙에 기록했다. DOC-03·담당 리뷰·CI는 남아 있으며 전체 계약 완료를 의미하지 않는다.
합성 fixture와 격리 PostgreSQL을 사용한다. 실제 Provider 호출은 필요하지 않다.

## 6. 계약 반영과 완료

구현·통합 검증과 담당 리뷰 후 이 변경분을 current OCR 구조화 계약의
Grounding 검증·부분 인식 절에 통합한다. 별도 current 계약을 복제하지 않고
이 Proposed 변경분은 제거하며 계약 인덱스와 Decision 링크도 최종 경로로 갱신한다.
Decision에는 실제 승인 근거와 검증 기준 SHA를 남긴다.

Template OCR #119·#143, 날짜 개선 #309, RAG DB 작업은 이 범위 밖이다.
문서 단계만으로 #144를 종료하지 않으며 PR 생성·리뷰 요청은 사용자가 진행한다.
