# #294 저장 계층 필드 방어 범위 Decision

- 상태: **Decided · 로컬 검증 완료 (PR #352)**
- 작성일: 2026-09-08
- 작성 담당: 송은영 (`phina-io`)
- 검토: 남한솔 (`solia142`) — Frontend placeholder 계약 정합성(PR #343), 김지혜 (`Jye-rookie`) — 담당 리뷰어
- 근거: [이슈 #294](https://github.com/AI-HealthCare-05/AH_05_04/issues/294), PR #352 리뷰 코멘트
- 상세 변경안: [OCR 구조화 계약 「부분 인식」](../../contracts/current/ocr-medication-structuring.md)

## 배경

#294는 OCR 완료 저장 경로(`ai_worker/adapters/sqlalchemy_ocr_result_store.py`)가 인식하지 못한
필수 필드의 row 자체를 만들지 않아, Frontend가 검수 입력 컨트롤을 만들 근거가 없어 처방 확정이
막히는 문제였다. 그런데 #144(PR #350)가 같은 문제를 구조화 계층(`validator.py`,
`prescription_ocr_structurer.py`)에서 이미 빈 검수 필드 생성으로 해결해, 두 계층에 유사한 로직이
생겼다. 리뷰에서 어느 계층이 정본인지, `MEDICATION_NAME`을 저장 계층에서도 채우는 게 맞는지 지적됐다.

## 결정과 이유

빈 검수 필드 생성의 정본은 구조화 계층이다. 저장 계층은 새 규칙을 만들지 않고, 아래 두 경우에
한해 회귀 방지용 방어 계층으로만 동작한다.

- `PRESCRIBED_DATE`(index 0): 규칙 기반 경로(`prescription_ocr_structurer.py`)는 날짜를
  전혀 인식하지 못하면 row 자체를 만들지 않는다 — #294의 실제 재현 시나리오이자 유일하게
  남는 gap이다. 저장 계층이 이 경우의 유일한 방어선이다.
- 이미 감지된 약품 행의 `DOSE_VALUE`·`FREQUENCY_PER_DAY`·`DURATION_DAYS`: 두 구조화 경로
  모두 이미 보장하므로 저장 계층의 채움은 사실상 중복이지만, 향후 구조화 계층 회귀가
  Frontend 입력 불가로 이어지는 것을 막는 저비용 방어로 유지한다.
- `MEDICATION_NAME`은 저장 계층에서 제외한다. 두 구조화 경로 모두 약품 행을 인식하는
  시점에 `MEDICATION_NAME`을 함께 만들고 grounding 실패 시 빈 필드 대체 없이 구조화
  전체를 실패시키므로, medication_index는 있는데 `MEDICATION_NAME` row만 없는 상태는
  발생하지 않는다. 계약도 `MEDICATION_NAME`을 빈 필드로 만들지 않도록 명시적으로
  금지하므로, 저장 계층이 이를 채우면 도달 불가능한 코드이면서 계약 위반이다.

## 영향 및 검토 근거

공개 API·DTO·DB 스키마 변경은 없다. `ai_worker/adapters/sqlalchemy_ocr_result_store.py`의
`_REQUIRED_MEDICATION_FIELD_TYPES`에서 `MEDICATION_NAME`을 제거하고, 관련 단위·통합 테스트를
갱신했다. 기존 결과 backfill과 처방 확정 검증 로직(`prescriptions.py`)은 변경하지 않는다.
