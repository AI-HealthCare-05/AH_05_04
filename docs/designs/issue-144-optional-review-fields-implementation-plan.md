# #144 Optional 빈 검수 필드 구현 계획·완료 기록

- 관련 이슈: [#144](https://github.com/AI-HealthCare-05/AH_05_04/issues/144)
- 상태: **1~5단계 로컬 구현·검증 완료 / 담당 리뷰·CI·병합 대기**
- 조사일: 2026-09-08
- 코드 기준: `develop`의 `7d4510f5f6e1f9f70fd25ce4bff30127fda41955`
- 구현 담당: 김지혜 (`Jye-rookie`), Worker/OCR 및 이슈 내 Backend 연결
- 리뷰: 송은영 (`phina-io`) — DB·PATCH 계약, 남한솔 (`solia142`) — DOC03 검수 화면 소비 확인

## 목적과 이번 단계 산출물

인식되지 않은 `MEDICATION_STRENGTH`·`DOSE_UNIT`에도 검수용 행과
`field_id`를 제공하여 기존 PATCH로 사용자가 입력할 수 있도록 한다.
이번 단계는 현재 코드·계약·테스트를 대조하고 후속 구현 경계를 기록한다.
현재 동작, 승인 상태 또는 #144 완료를 변경하는 문서가 아니다.

## 현재 구조와 변경 지점

| 영역 | 현재 코드에서 확인한 내용 | 후속 구현·검증 방향 |
| --- | --- | --- |
| LLM 변환 | [validator](../../backend/app/services/ocr_ai/validator.py)의 `_EMPTY_REVIEW_FIELD_TYPES`에 함량·단위가 없다. 생성값 누락과 `_make_field()`의 grounding 실패 모두 집합에 없는 필드를 생략한다. | 두 필드를 빈 검수 대상에 포함한다. 누락과 grounding 실패를 각각 검증한다. |
| 빈 필드 표현 | 같은 파일의 `_make_empty_review_field()`가 raw/normalized/version/confidence 네 값을 모두 `None`으로 생성한다. | 기존 표현을 유지하며 실패한 추정값을 복사하지 않는다. |
| 규칙 구조화 | [실제 구현](../../ocr_runtime/prescription_ocr_structurer.py)의 `_structure_medication_row()`는 인식된 함량·단위만 반환한다. `structure()`는 `_extract_medication_rows()`가 찾은 행을 순회한다. | 식별된 약품 행에 누락된 검수 필드만 보충한다. 행 탐지 기준을 바꾸거나 새 약품 행을 만들지 않는다. |
| Backend 진입점 | [Backend structurer](../../backend/app/services/prescription_ocr_structurer.py)는 공통 runtime 구현을 재노출하는 shim이다. | 실제 수정은 runtime에 적용한다. #309의 날짜 함수 재노출 수정은 섞지 않는다. |
| 공유 Provider 필드 | [RecognizedField](../../provider_contracts/ocr.py)는 raw/confidence 및 정규화 metadata의 null을 허용한다. | 신규 DTO나 필드 enum 없이 기존 표현을 사용한다. Worker 전달·저장에서도 null이 유지되는지 검증한다. |
| DB | [ExtractedField](../../backend/app/models/ocr.py)는 네 값의 null을 허용한다. `(ocr_job_id, medication_index, field_type)` unique와 index 범위 CHECK가 있다. | 생성 단계에서 동일 identity가 중복되지 않게 한다. 현재 모델상 신규 컬럼은 불필요하며 실제 migration 스키마는 4단계에서 확인한다. |
| 저장 | [OcrRepository.replace_fields](../../backend/app/repositories/ocr_repository.py)는 입력 필드마다 DB 행을 만든다. | 빈 필드도 저장·조회되어 `field_id`를 갖는지 통합 검증한다. PATCH 시 누락 행을 생성하는 upsert는 추가하지 않는다. |
| PATCH·확정 | [OCR 서비스](../../backend/app/services/ocr.py)의 `update_extracted_field()`는 기존 소유 필드를 갱신한다. 모델은 함량·단위·TIMING의 null 확정을 허용한다. | 빈 필드의 값 입력 및 null 확정을 검증하고, 필수 필드 null 거부·소유권·처방 확정 후 수정 차단을 유지한다. |
| 현재 계약 | [OCR 구조화 계약](../contracts/current/ocr-medication-structuring.md)의 Grounding 검증·부분 인식 절에 함량·단위 생략과 규칙 경로 미생성이 명시되어 있다. | 동작 구현과 함께 해당 절 및 계약 인덱스를 정렬한다. 이번 조사에서는 current 문서를 선제적으로 바꾸지 않는다. |

## 2단계 결과 — 변경안 정리 완료

[Proposed 변경안](../contracts/current/ocr-medication-structuring.md)과
[Decision 초안](../governance/decisions/2026-09-08-ocr-empty-review-fields-144.md)에
생성 대상·null·중복·기존 값 보존·저장·PATCH 경계 및 ER-01~12 인수 항목을 정리했다.
현재 실행 계약과 코드의 동작은 변경하지 않았다.

이슈의 두 경로 기준 통일 및 규칙 경로 DOSE_VALUE·DOSE_UNIT 동시 보충 요구를
근거로, 기존 LLM 네 유형에 함량·단위를 더한 여섯 유형을 공통 보충 대상으로 제안한다.
처방일은 별도 정책을 유지한다. 상세 규칙은 Proposed 변경안에서만 관리한다.

문서 링크·전체 diff·공백 검사를 수행한다. 2단계 역시 문서 변경이므로
동작 테스트·Ruff·Mypy·migration은 실행하지 않는다. 담당 리뷰 승인과 구현 검증은 남아 있다.

## 3단계 결과 — 구조화 구현·단위 회귀 완료

LLM은 공통 보충 대상에 함량·단위를 포함했고, 규칙 경로는 약품명이 생성된
행의 누락 유형만 순서대로 보충한다. 공통 대상은 `ocr_runtime/review_fields.py`에서
관리한다. 기존 정상값·행 탐지·날짜 정책을 유지한다.

[검증 증빙](../testing/optional-review-fields-144.md)에 179개 테스트 결과와
실행 범위를 기록했다. 실제 DB 저장·PATCH·DOC-03 확인은 4~5단계에 남아 있다.
current 계약 승격 및 담당 승인 완료를 선언하지 않는다.

## 4단계 결과 — 저장·API 통합 검증 완료

Backend 53개·Worker PostgreSQL 3개 테스트와 실제 Alembic 적용·제약 조회를 완료했다.
빈 필드 값 입력·null 확정·중복 저장 실패 rollback을 검증했고 신규 migration은 필요하지 않았다.
[실행 범위와 재현 명령](../testing/optional-review-fields-144.md)을 참고한다.
DOC-03·담당 리뷰·CI·current 계약 정렬은 5단계에 남아 있다.

## 단계별 완료 기준

| 단계 | 작업 | 완료 기준 |
| --- | --- | --- |
| 1 — 현재 상태 조사 | 코드·계약·DB 모델·기존 테스트 대조 | 이 문서 커밋. 동작 변경 없음. |
| 2 — 변경 계약 정리 | 경로별 보충 대상, index·null·중복·보존 규칙 명문화 | Proposed/현재 동작을 구분한 계약 변경안 및 검증 항목 정리. |
| 3 — 구조화 구현 | LLM 대상 확장 및 규칙 경로 보충 | 두 경로의 누락·실패·값 보존·행 비생성 회귀 통과. |
| 4 — 저장·API 통합 | 저장→조회→기존 PATCH→처방 확정 검증 | 격리 PostgreSQL에서 field_id·null·unique·소유권·확정 제약 확인. |
| 5 — 문서·인계 | 실제 결과로 계약·Decision·증빙 갱신 | 관련 검사 및 CI 결과, 기준 SHA, 담당 리뷰 요청용 PR 초안 정리. |

## 회귀 검증 계획

| 검증 | 기존 출발점 | 추가 확인 |
| --- | --- | --- |
| LLM 누락·grounding | [validator tests](../../backend/app/tests/ocr_ai/test_validator.py)의 빈 TIMING·횟수 처리 | 함량·단위 각각 누락/근거 실패/유효값, 다른 행 근거 거부, 네 값 null |
| 규칙 부분 인식 | [structurer tests](../../backend/app/tests/ocr/test_prescription_ocr_structurer.py)의 이름만 인식한 두 번째 행, 안내문 배제 사례 | 누락 필드 보충, 기존 값 보존, 중복 없음, 헤더/약품 행 없는 입력에서 행 비생성 |
| 구조화 어댑터 | [structurer adapter tests](../../backend/app/tests/ocr/test_structurer.py) | 경로 변경 후 출력 필드의 연결과 기존 정상값 유지 |
| DB 저장 | [repository tests](../../backend/app/tests/ocr/test_ocr_repository.py) | 빈 필드 저장·field_id 조회·identity unique 및 null 제약 |
| 사용자 검수 | [confirmation API tests](../../backend/app/tests/ocr/test_prescription_confirmation_api.py)의 optional null 확정 | 기존 테스트는 인식된 함량을 null 확정하는 사례다. 새로 생성한 빈 함량·단위의 PATCH와 null 확정을 각각 추가 |
| 처방 검증 | [confirmation validation tests](../../backend/app/tests/ocr/test_prescription_confirmation_validation.py) | 필수값 누락 거부 유지, 미확인 OCR 값이 확정값으로 사용되지 않음 |
| Worker 저장 경계 | [Worker result store tests](../../ai_worker/tests/core/test_sqlalchemy_ocr_result_store.py) | 공통 구조화 결과의 빈 값 전달·저장 및 기존 쓰기 가능 상태 제약 유지 |

기존 규칙 테스트 중 필드 집합이 약품명·함량뿐임을 단언하는 부분은
변경 계약에 맞춰 수정해야 한다. 기대값만 늘리지 않고 약품 행 수와 인식값 보존을
함께 검사한다. fixture는 합성 데이터만 사용하며 외부 OCR/LLM 호출은 필요하지 않다.

## 범위와 의존성

- #119·#143의 Template OCR 보류와 독립적으로 General OCR 두 경로를 대상으로 한다.
- #309의 날짜 라벨·좌표 판별과 shim 개선은 별도 이슈에 남긴다.
- 새 약품 행 생성, 프롬프트·grounding 정확도 개선, unmatched token 저장은 제외한다.
- #164·#165·#166의 RAG DB 계약을 기다릴 필요는 없다.
- DB migration 필요 여부는 현재 모델만으로 확정하지 않는다. 구현 단계에서 실제
  Alembic 스키마와 PostgreSQL 검증 결과에 따라 판단한다.

## 이번 단계 검증과 한계

- 코드·현재 계약·기존 테스트를 정적으로 대조했다.
- 이 변경은 조사 문서 한 개에 한정한다. 문서 참조 경로, 내용 및 전체 diff를 확인하고
  `git diff --check`로 공백 오류를 검사한다.
- 동작 테스트·Ruff·Mypy·migration은 이번 문서 단계에서 실행하지 않았다.
- Backend 테스트의 autouse fixture는 PostgreSQL 스키마를 drop/create하므로
  후속 실행은 사용자 DB와 분리된 테스트 DB에서 수행한다.
- 기존 테스트의 존재는 최신 기준에서의 통과 증빙이 아니다. 실행 결과와 SHA는
  3~5단계에서 실제 검증 후 기록한다.


## 5단계 결과 — 로컬 마무리

기존 OCR 구조화 계약에 변경분을 통합하고 중복 Proposed 변경안은 제거했다.
Decision은 실제 승인 근거가 없으므로 Proposed로 유지한다. 앞 단계 기록의 미구현·
검증 대기 문구는 해당 단계 당시 상태이며 최종 결과는 이 절과 검증 증빙을 기준으로 한다.

최신 develop `bd6b4d6bc2d2a04d24bd88012dbaea91ee1aa3fb` 병합 후 OCR 302개,
Worker 공통·OCR·RAG 1120개, PostgreSQL Worker 3개가 통과했다.
전체 Ruff·포맷 512개·Mypy 429개 source 파일 검사를 통과했다.
실제 UI 확인·담당 승인·CI 결과는 아직 없으며 #144 종료를 선언하지 않는다.
PR은 사용자가 생성하고 송은영 API·DB 리뷰 및 남한솔 DOC-03 확인을 받는다.
