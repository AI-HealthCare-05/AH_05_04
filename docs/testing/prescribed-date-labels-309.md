# #309 처방일 라벨·값 연결 검증 기록

- 관련 이슈: #309
- 구현: 김지혜 (`Jye-rookie`), 리뷰: 송은영 (`phina-io`)
- 상태: #350 반영 후 #353 리뷰 보완 진행. 현재 선택·검수 동작은 마지막 리뷰 반영 절을 기준으로 한다. 앞선 단계 기록은 당시 증빙이다.
- 기준 develop: `bd6b4d6bc2d2a04d24bd88012dbaea91ee1aa3fb`
- 실행일: 2026-09-08

## 1단계 재현 기록 (수정 전)

아래 실패 2건은 최초 재현 당시 결과이며, 2단계에서 해결했다.

### 합성 입력

생년월일 라벨과 2000년 이후 날짜 값을 서로 다른 박스로 같은 줄에 배치한다.
교부일자는 별도 줄에 두고 라벨·값이 같은 박스인 경우와 분리된 경우를 각각 검증한다.
좌표와 값은 고정한 채 입력 배열 순서만 뒤집어 총 4개 사례를 비교한다.
실제 환자 정보·외부 OCR 호출은 사용하지 않는다.

### 당시 관찰 결과

| 배열 순서 | 교부일자 박스 | 기대 | 수정 전 결과 |
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


## 4단계 — 완료 조건 대조와 최종 검증

#349가 포함된 develop `16059da71a993de329183a176c1a8c560301fb30`를 병합했다.
이슈는 내부 파싱 버그 수정으로 명시되어 있으며, API·DTO·DB·필수값·상태·공개 조건을 변경하지 않는다.
기존 `date-rule-v1` 정규화 형식과 원문·confidence를 유지하므로 새 공유 계약을 만들거나
미병합 동작을 current 계약의 완료 상태로 승격하지 않는다.

| #309 완료 조건 | 구현·검증 근거 |
| --- | --- |
| 좌표 기반 분리 라벨 연결 | `_nearby_date_label_kind`: center_x/center_y/height로 인접 왼쪽·위 라벨 선택 |
| 분리된 생년월일 제외 | `test_structure_excludes_birthdate_with_separate_label`, `test_structure_excludes_date_below_birthdate_label` |
| 교부일자·발행일 등 우선 | `test_structure_prefers_labeled_date_to_unlabeled_date` 및 2단계 규칙 설명 |
| 2000년 이후 생년월일 재현 회귀 | inline/split 교부일자와 배열 순서 반전의 4가지 조합 |
| #296/#298 기존 날짜 회귀 | 기존 structurer 테스트와 OCR·OCR AI 전체 회귀 |
| Backend shim 재노출 | `normalize_prescribed_date_text` 재노출, validator의 shim import 및 같은 함수임을 검증 |

### 선택 규칙과 검증 한계

같은 박스 라벨 → 인접 라벨 연결 → 제외 후보 제거 → 선호 라벨 날짜 우선 순서다.
동일 거리의 선호·제외 라벨은 제외하며, 서로 다른 선호 날짜가 공존하면 날짜를 반환하지 않는다.
같은 선호 날짜의 반복은 원문 문자열 순서로 선택한다. 선호 라벨이 없으면 기존 첫 유효 후보를 유지한다.
원문 생년월일만 있고 라벨이 아예 인식되지 않은 상황을 좌표만으로 판별하는 기능은 아니다.
거리 기준은 합성 fixture에 대한 휴리스틱이며 실제 처방전 레이아웃 전체 정확도를 보증하지 않는다.
LLM 검증기의 날짜 형식 검사에 좌표 라벨 판정을 새로 적용하지 않는다.

### 최종 로컬 결과

검증 기준: `82b5d6e`(#349까지 develop 반영)의 코드와 이 절을 포함한 문서 변경.

| 검사 | 결과 |
| --- | --- |
| Backend OCR·OCR AI·OCR 필드 별칭 계약 | 312 passed |
| Worker core·OCR·RAG·evaluation | 2,086 passed, 8 skipped |
| 전체 Ruff·format | 통과, 511 files |
| Mypy Backend·Worker·ocr_runtime | 통과, 434 source files |
| Alembic revision graph | `165f90716263` 단일 head |
| git diff --check | 통과 |

```bash
# Python 3.13, Backend는 임시 PostgreSQL 16 test DB 사용
DB_HOST=127.0.0.1 DB_PORT=55446 DB_EXPOSE_PORT=55446 DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=test PYTHONPATH=backend:. python -m pytest backend/app/tests/ocr backend/app/tests/ocr_ai tests/contract/test_ocr_provider_field_alias_contract.py -q
PYTHONPATH=. python -m pytest ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation -q
ruff check .
ruff format . --check
MYPYPATH=backend:. python -m mypy backend/app ai_worker ocr_runtime
PYTHONPATH=backend:. python -m alembic -c backend/alembic.ini heads
git diff --check
```

Backend는 기존 모델 생성 fixture로 격리 DB를 초기화했으며 종료 후 테스트 컨테이너와 볼륨을
삭제했다. 사용자 DB·실제 OCR/LLM·환자 데이터는 사용하지 않았다. migration 변경이 없으므로
upgrade/rollback을 재실행하지 않았고 revision graph만 확인했다. 전체 Backend·Redis 통합,
Frontend·GitHub CI는 이번 로컬 결과에 포함하지 않는다. PR 생성 후 최신 HEAD CI를 확인한다.
#350/#144는 기준 develop에 아직 포함되지 않아 두 변경을 결합한 검증 완료를 주장하지 않는다.

## #353 충돌 해결 — #350 병합 후 통합 확인

develop `50d1f7d`(#348·#329·#350 포함)을 #353에 반영했다. validator import 충돌은
Backend 날짜 shim과 공통 `EMPTY_REVIEW_FIELD_TYPES`를 함께 유지했고, structurer 테스트
충돌은 #309 날짜 회귀와 #144 누락 검수 필드 회귀를 모두 보존했다. 앞선 '#350 미포함' 표기는
이전 검증 기준이며 아래는 두 변경을 결합한 로컬 결과다.

- Backend OCR·OCR AI·OCR 필드 별칭 계약: **328 passed**, 격리 PostgreSQL 16.
- Worker OCR·SQLAlchemy OCR 저장소 단위 테스트: **36 passed**.
- 전체 Ruff·format: 통과, **528 files**.
- Mypy Backend·Worker·ocr_runtime: 통과, **451 source files**.
- Alembic revision graph: `165f90716263` 단일 head. migration 실행 검증은 재실행하지 않음.
- `git diff --check` 통과. 전체 서비스 CI는 충돌 해결 커밋 push 후 확인.

검증 명령은 위 4단계 Backend 명령과 동일한 범위이며, Worker는
`pytest ai_worker/tests/ocr ai_worker/tests/core/test_sqlalchemy_ocr_result_store.py -q`를 실행했다.
테스트용 PostgreSQL은 별도 컨테이너의 test DB만 사용하고 종료 후 정리했다.


## #353 은영님 리뷰 반영 — 날짜 검수 경로 복구

기준: `a19d31b`(#350 포함)과 이 절에 동반되는 리뷰 수정. 과거 #350 미포함 설명이나
`1e44682` 검증 수치를 현재 결과로 사용하지 않는다.

### 수정한 동작

- 규칙 경로에서 날짜가 없거나, 생년월일 제외·유효하지 않은 날짜·선호 날짜 충돌로
  선택하지 못해도 index 0의 `PRESCRIBED_DATE` 필드를 하나 생성한다.
  원문·정규화값·정규화 version·confidence는 null이다. 생년월일 원문을 빈 필드에 복사하지 않는다.
- 기존 저장·조회 API가 field_id를 제공하므로 사용자는 기존 PATCH로 날짜를 입력할 수 있다.
  입력 전 처방 확정은 REQUIRED로 차단하며 입력 후 기존 확정 경로를 사용한다.
- 인접 범위에서 선호·제외 라벨이 경쟁하면 단순 최단 거리로 의미를 결정하지 않는다.
  가까운 생년월일 라벨을 가진 값을 처방일로 강제 채택하지 않고 빈 필드로 수동 검수한다.
  날짜와 라벨이 같은 박스라면 기존 명시적 라벨 판정을 유지한다.
- 리뷰의 `(350,300) 처방일자 / (480,300) 생년월일 / (500,300) 날짜` 재현은
  자동 정답 선택으로 해결했다고 주장하지 않는다. 해당 좌표만으로 어느 라벨이 실제 소속인지
  증명할 수 없으므로 검수 필드가 사라지는 문제를 해결하고 사람이 입력하는 경로로 남긴다.
- 약품이 없는 입력에 약품 행을 만들지 않는다. 처방일 빈 필드만 생성하며
  #144의 약품별 빈 검수 필드 및 기존 날짜 형식은 유지한다.

### 검증

| 범위 | 결과 |
| --- | --- |
| structurer 순수 회귀 | 122 passed |
| Backend OCR·OCR AI·필드 별칭 계약 | 342 passed, 격리 PostgreSQL 16 |
| Worker OCR·OCR 저장소 단위 | 36 passed |
| 전체 Ruff·format | 통과, 528 files |
| Mypy Backend·Worker·ocr_runtime | 통과, 451 source files |
| git diff --check | 통과 |

Backend의 실제 저장·GET field_id·PATCH·처방 확정 회귀는 인위적으로 정상 날짜를
끼워 넣던 기존 테스트를 수정해 구조화 결과를 그대로 저장한다. 날짜 누락·생년월일 제외·
선호 날짜 충돌·밀집 라벨 입력을 포함한다. 날짜 미입력 시 REQUIRED 차단과 입력 후 확정도
같은 테스트에서 확인했다. 해당 10개 사례는 마지막 검증 보강 후 재실행하여 **10 passed**였다.
DB 테스트는 127.0.0.1:55448의 일회용 합성 test DB만 사용한다.
전체 Backend·Redis·Frontend·GitHub CI 및 migration 재실행 결과는 이 표에 포함하지 않는다.

### 후속 검토가 필요한 한계

- LLM 경로의 prescribed_date grounding은 날짜 원문 존재·형식 확인이며 생년월일 의미를
  좌표/라벨로 배제하는 기능이 아니다. 이번 수정의 보호 범위는 규칙 경로다.
  LLM 경로에 대한 Source label 검증·prompt 지시·불확실 날짜 수동 검수 회귀를 후속으로 검토한다.
- `생년훨일` 등 제외 라벨의 OCR 오탈자/누락은 현재 정확한 문자열 패턴으로 감지하지 못한다.
  퍼지 매칭을 임의로 추가하면 다른 라벨을 잘못 제외할 수 있어 별도 합성 사례·수용 기준이 필요하다.
- 후속 이슈는 아직 생성하지 않았다. 사용자가 생성할 제목 제안:
  `fix: LLM OCR 처방일의 생년월일 오인식 방지 및 라벨 손상 시 검수 기준 보강`.
  담당은 김지혜, Backend·DB/검수 경계 리뷰는 송은영이며 #309/#353에 연결한다.
