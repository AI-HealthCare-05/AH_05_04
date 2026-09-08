# #309 처방일 라벨·값 연결 — 1단계 재현

- 관련 이슈: #309
- 구현: 김지혜 (`Jye-rookie`), 리뷰: 송은영 (`phina-io`)
- 상태: 재현 완료, 동작 수정 전. 실패 테스트 2건이 남아 있는 중간 커밋.
- 기준 develop: `bd6b4d6bc2d2a04d24bd88012dbaea91ee1aa3fb`
- 실행일: 2026-09-08

## 합성 입력

생년월일 라벨과 2000년 이후 날짜 값을 서로 다른 박스로 같은 줄에 배치한다.
교부일자는 별도 줄에 두고 라벨·값이 같은 박스인 경우와 분리된 경우를 각각 검증한다.
좌표와 값은 고정한 채 입력 배열 순서만 뒤집어 총 4개 사례를 비교한다.
실제 환자 정보·외부 OCR 호출은 사용하지 않는다.

## 관찰 결과

| 배열 순서 | 교부일자 박스 | 기대 | 현재 결과 |
| --- | --- | --- | --- |
| 생년월일 먼저 | 라벨·값 함께 | 2026-08-12 | 2010-03-15, 실패 |
| 생년월일 먼저 | 라벨·값 분리 | 2026-08-12 | 2010-03-15, 실패 |
| 교부일자 먼저 | 라벨·값 함께 | 2026-08-12 | 기대와 일치 |
| 교부일자 먼저 | 라벨·값 분리 | 2026-08-12 | 기대와 일치 |

라벨과 값이 분리되면 기존 제외 라벨 필터가 값 박스를 제외하지 못하고,
현재 첫 날짜 선택 방식이 입력 순서에 따라 결과를 바꾸는 것을 확인했다.

## 검증

```bash
PYTHONPATH=backend:. python -m pytest --confcutdir=backend/app/tests/ocr backend/app/tests/ocr/test_prescription_ocr_structurer.py -q --tb=short
# 2 failed, 95 passed
```

기존 93개는 통과하고 신규 4개 중 2개가 실패했다. 실패는 목표 동작에 대한 재현 증거이며
xfail/skip으로 숨기지 않았다. `--confcutdir`는 이 순수 구조화 테스트에 불필요한
상위 DB 생성·삭제 fixture를 제외한다. DB·전체 CI 검증을 의미하지 않는다.
변경 테스트 Ruff·포맷·공백 검사를 수행했다.

다음 단계에서 좌표 기반 라벨 연결·제외·선호 라벨 우선순위를 구현하여 실패 2건을
해결한다. 기존 날짜 형식·약품 구조화 회귀를 유지하고, 라벨 경쟁·거리 제한도 보강한다.
#350의 #144 변경은 이 기준 develop에 포함되지 않았으며 병합되면 통합 검증한다.

## 2단계 — 좌표 기반 선택 구현

- 기존 재현 실패 2건 해결, structurer 테스트 **106 passed**.
- 변경 파일 Ruff·Mypy 검사 및 공백 검사 통과.
- 신규 API·DTO·DB 변경 없음. shim 재노출은 3단계에 남긴다.

같은 박스의 명시적 라벨을 먼저 확인하고, 날짜를 포함하지 않는 별도 라벨을
날짜 값 주변에서 찾는다. 높이는 두 박스 높이 중 작은 값을 사용한다.
같은 줄 왼쪽은 가로 거리 12배 이하·세로 차이 0.75배 이하, 바로 위는
가로 차이 2배 이하·세로 거리 3배 이하로 제한한다. 0 이하 높이는 연결하지 않는다.
이 수치는 합성 배치에 대한 구현 기준이며 다양한 실제 레이아웃 정확도 측정 결과가 아니다.

가장 가까운 라벨을 선택하며 동일 거리에서 종류가 충돌하면 제외한다.
생년월일·생일·주민번호 라벨을 제외하고 교부일자·발행일·처방일 라벨을 우선한다.
서로 다른 날짜에 선호 라벨이 있으면 임의로 하나를 선택하지 않고 날짜를 반환하지 않는다.
선호 라벨이 없으면 제외되지 않은 후보의 기존 첫 매치 동작을 유지한다.
라벨 자체가 인식되지 않은 날짜의 의미를 확실히 구분하는 문제는 남는다.
원문·confidence와 date-rule-v1 날짜 정규화 형식은 유지한다.

추가 검증: 선호 라벨 3종, 위쪽 생년월일 라벨, 먼/아래 라벨 연결 방지,
가까운 라벨 선택·동률 제외, 선호 날짜 충돌 및 입력 순서 반전.
이는 로컬 2단계 완료이며 전체 OCR 회귀·호환 import·계약 정렬·최종 검사는 3~4단계에 수행한다.

## 3단계 — Backend 호환 경로·전체 OCR 회귀

`backend/app/services/prescription_ocr_structurer.py`에서
`normalize_prescribed_date_text`를 재노출하고 LLM validator의 import를 이 경로로 변경했다.
날짜 정규화 자체는 같은 runtime 함수를 사용하며, 한글·라벨 포함 날짜와
존재하지 않는 날짜·추가 숫자 거부를 4개 사례로 검증했다.

- Backend OCR·OCR AI: **303 passed** (격리 PostgreSQL 16)
- Worker OCR·OCR 필드 별칭 계약: **41 passed**
- 변경 코드 Ruff·포맷·공백 검사: 통과
- Mypy 변경 구현 3개 파일: 통과

```bash
# 전용 합성 PostgreSQL만 대상으로 실행
export DB_HOST=127.0.0.1 DB_PORT=55445 DB_EXPOSE_PORT=55445
export DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=test PYTHONPATH=backend:.
python -m pytest backend/app/tests/ocr backend/app/tests/ocr_ai -q
python -m pytest ai_worker/tests/ocr tests/contract/test_ocr_provider_field_alias_contract.py -q
```

DB 테스트는 기존 Backend 모델 생성 fixture를 사용했다. 이번 변경은 schema를 수정하지
않으므로 migration 재적용은 하지 않았다. 외부 OCR/LLM 호출·운영 데이터는 사용하지 않았다.
#144/#350은 아직 이 작업 브랜치에 포함하지 않았으며 함께 병합된 상태의 회귀 결과는 아니다.
4단계에서 최신 develop 확인·계약 문서·최종 검사·PR 초안을 정리하고 한 번에 푸시한다.
