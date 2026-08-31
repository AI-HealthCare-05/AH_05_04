# OCR 약품 행 구조화 계약

> 상태: Current runtime. PR #96에서 feature flag 기반 비-RAG LLM 구조화 경로가 구현됐고 기본값 `false`는 미구현이 아니라 기본 비활성화를 뜻한다. Approved v4에서 추가한 최소 전송 allowlist, raw/rule/draft/corrected/confirmed provenance, Worker 이관과 실패 복구는 [OCR 비-RAG LLM 구조화 목표 계약](../targets/post-mvp-1/ocr-llm-structuring-v1.md)의 미구현 범위다.

## 목적

OCR 원문을 약품별 필드로 구조화하면서 정상 약품 누락과 안내문
오탐을 함께 최소화한다. 구조를 확신할 수 없는 값은 자동 확정하지
않고 사용자 확인 대상으로 유지한다.


## 현재 구조화 경로

- feature flag 기반 LLM 경로와 규칙 기반 fallback은 현재 Backend 실행 경로에 구현되어 있습니다.
- `OCR_STRUCTURE_LLM_ENABLED`의 기본값은 `false`입니다.
- 비활성화 상태에서는 CLOVA OCR token을 기존 규칙 기반 구조화기로 처리하며 OpenAI에 전달하지 않습니다.
- 활성화 상태에서만 CLOVA OCR 전체 token을 OpenAI Responses API Structured Outputs에 전달합니다.
- LLM 구조화 결과는 `ocr-structure-prompt-v2` 스키마를 따르며 OpenAI 요청은 `store=False`로 실행합니다.
- 규칙 기반 경로에서는 OCR 작업의 `model_version`과 `prompt_version`을 `null`로 기록합니다.
- LLM 경로에서는 실제 모델 ID와 프롬프트 버전을 기록합니다.
- Production Compose도 해당 설정의 기본값을 `false`로 유지합니다.

## Timeout 계약

다음 기호를 사용합니다.

- `C`: `CLOVA_OCR_TIMEOUT_SECONDS`, 기본 20초
- `S`: `OCR_STRUCTURE_TIMEOUT_SECONDS`, 기본 30초
- `E`: `OCR_STRUCTURE_LLM_ENABLED`가 `true`이면 1, 아니면 0
- `M`: HTTP 요청 종료와 실패 상태 저장을 위한 처리 여유, 기본 참고값 5초

CLOVA OCR과 OpenAI 구조화는 순차 실행되므로 OCR 요청의 Provider timeout 기준은 `C + E × S`입니다. 상위 HTTP client와 reverse proxy의 read timeout은 최소 `C + E × S + M` 이상이어야 합니다.

기본값 기준으로 LLM 구조화가 비활성화되면 25초 이상, 활성화되면 55초 이상이 필요합니다. 개별 timeout 값을 변경하면 합산 기준도 함께 다시 계산해야 합니다.

## 제품 함량과 복용량 구분

| 필드 | 의미 | 예시 |
| --- | --- | --- |
| `MEDICATION_NAME` | 처방전에 기재된 약물명 또는 성분명 | `복합정` |
| `MEDICATION_STRENGTH` | 제품 자체의 함량 | `100mg`, `5mg/100mg`, `1mg/mL`, `500mg/5mL` |
| `DOSE_VALUE` | 실제 1회 복용량 값 | `1` |
| `DOSE_UNIT` | 실제 1회 복용 단위 | `정` |

- 제품 함량은 선택값입니다.
- 소수점과 복합 함량의 `/`는 값의 일부로 보존합니다.
- 제품 함량이 없거나 grounding에 실패해도 약물 행 전체를 실패시키지 않습니다.
- 사용자가 확인한 제품 함량만 확정 처방의 `medication.strength_text`로 저장합니다.
- LLM 경로와 규칙 기반 경로 모두 약품명 끝에서 식별한 제품 함량을 `MEDICATION_STRENGTH`로 분리합니다.
- `MEDICATION_NAME`에는 분리된 제품 함량을 중복해서 포함하지 않습니다.
- 제품 함량은 원문을 `raw_value`로 보존하며 현재 `normalized_value`와 `normalization_version`을 생성하지 않습니다.

## Grounding 검증

- LLM이 반환한 값은 함께 반환된 CLOVA `source_ids`를 기준으로 검증합니다.
- 존재하지 않는 token을 참조한 값은 저장하지 않습니다.
- OCR 원문에서 근거를 확인할 수 없는 값은 확정값으로 저장하지 않습니다.
- 공백으로 분리된 token의 결합, 복용 조건의 나열 구분자 차이 및 날짜 구분자 차이는 허용합니다.
- 약물명 grounding 실패는 약제 행 식별 안전을 위해 OCR 구조화 전체 실패로 처리합니다.
- grounding 실패는 필드 종류에 따라 다음과 같이 처리합니다.
  - `PRESCRIBED_DATE`, `DOSE_VALUE`, `FREQUENCY_PER_DAY`, `DURATION_DAYS`: 사용자 입력용 빈 검수 필드
  - `TIMING`: 선택값이지만 검수 편의를 위한 빈 검수 필드
  - `MEDICATION_STRENGTH`, `DOSE_UNIT`: 필드 생략
- 빈 검수 필드의 `raw_value`, `normalized_value`, `normalization_version`, `confidence_score`는 모두 `null`입니다.

현재 validator는 `source_ids`의 존재 여부와 OCR 원문 근거를 필드별 기준으로 검증합니다.

- `DOSE_VALUE`, `FREQUENCY_PER_DAY`, `DURATION_DAYS`는 더 큰 숫자의 일부가 아닌 완전한 숫자 경계로 검증합니다.
- `FREQUENCY_PER_DAY`는 `회`, `번` 문맥의 숫자만 근거로 인정합니다.
- CLOVA가 숫자와 단위를 별도 token으로 반환한 경우, 횟수·기간 필드는 숫자와 의미 단위 token을 모두 `source_ids`로 참조해야 합니다.
- 단독 숫자 token만으로는 횟수와 기간 문맥을 구분할 수 없으므로 `FREQUENCY_PER_DAY`, `DURATION_DAYS`의 근거로 인정하지 않습니다.
- `DURATION_DAYS`는 `N일`, `N일분`, `N일간`, `N days`처럼 실제 기간 문맥의 숫자만 인정하며, `1일 N회`의 `1일`은 기간 근거로 인정하지 않습니다.
- `MEDICATION_NAME`은 OCR 약품명 전체와 일치해야 합니다.
- 하나의 OCR token에 약품명과 제품 함량이 함께 있는 경우에는 약품명 뒤에 유효한 제품 함량만 남는 것을 허용합니다.
- 공백으로 분리된 OCR token 결합과 필드별로 허용된 표기 차이만 인정합니다.

### 약제 행 인접성 검증

- 각 약품명의 `source_ids`가 참조하는 OCR token 좌표를 해당 약제 행의 anchor로 사용합니다.
- 약제별 선택 필드는 현재 약제 anchor에 가장 가깝고, token 높이 기준 허용 거리 안에 있는 경우에만 저장합니다.
- 다른 약제 행이 더 가깝거나 같은 거리인 token은 현재 약제의 근거로 인정하지 않습니다.
- 하나의 필드가 여러 약제 행의 token을 함께 참조하면 grounding 실패로 처리합니다.
- 동일한 약품명 OCR token을 둘 이상의 약제에서 공유할 수 없습니다.
- 약품명은 OCR 줄바꿈을 고려하여 허용된 인접 행 결합을 지원하지만, 허용 거리를 넘는 행의 token 결합은 거부합니다.
- 일반 약제 필드는 token 높이 대비 세로 중심점 거리 `0.75` 이하를 같은 행으로 인정합니다.
- 연속 약품명 행은 token 높이 대비 세로 중심점 간격 `1.5` 이하를 허용합니다.

### 적용 경로

이 grounding 검증은 현재 CLOVA General OCR token을 OpenAI Structured Outputs로
변환한 LLM 구조화 결과에만 적용합니다.

규칙 기반 경로는 별도의 `PrescriptionOcrStructurer`를 사용하며
`validate_and_convert_draft()`를 호출하지 않습니다.

CLOVA Template OCR 전용 변환기는 아직 구현되지 않았습니다.
Template OCR 적용과 기존 `RecognizedField` 계약으로의 변환은
후속 구현 Issue 및 PR에서 처리합니다.

## medication_index

- `0`은 처방일자처럼 특정 약품에 속하지 않는 문서 공통 필드다.
- `1` 이상은 처방전 표에서 위에서 아래 순서로 식별한 약품 행이다.
- 동일한 `medication_index`의 필드는 동일한 약품 행에서 추출돼야 한다.
- 서로 다른 물리적 행의 값을 임의로 하나의 약품에 연결하지 않는다.
- 연속 약품명으로 판정된 행만 직전 약품과 동일한 index를 사용한다.

## 부분 인식

- 약품명과 일부 필드만 인식된 경우에도 약품 행을 삭제하지 않는다.
- 규칙 기반 경로에서는 인식되지 않은 필드를 생성하지 않습니다.
- LLM 경로에서는 검수용 빈 필드 대상으로 정한 `PRESCRIBED_DATE`, `DOSE_VALUE`, `FREQUENCY_PER_DAY`, `DURATION_DAYS`, `TIMING`만 빈 필드로 생성할 수 있습니다.
- 값이 없는 `MEDICATION_STRENGTH`와 `DOSE_UNIT`은 생성하지 않습니다.
- OCR 원문이 있는 필드는 `raw_value`로 보존합니다.
- `I정`, `I회`처럼 숫자 오인식이 의심되더라도 자동으로 `1`로 확정하지 않는다.
- 모든 추출 필드는 기본적으로 `UNCONFIRMED` 상태다.
- 최종 처방 확정에는 사용자가 확인한 `confirmed_value`만 사용한다.

## 미확인 후보

현재 구현은 약품명과 일부 구조적 근거를 확인할 수 있는 행을
`UNCONFIRMED` 추출 필드로 유지한다.

`I정`, `I회`처럼 숫자 오인식이 의심되는 값은 자동 교정하지 않고
OCR 원문을 `raw_value`로 보존한다.

다만 구조화 규칙으로 필드 종류를 결정할 수 없는 원문과 제외된 행의
좌표를 별도 후보로 DB에 저장하고 OCR 조회 API에서 반환하는 기능은
현재 지원하지 않는다.

이 기능은 후속 작업에서 다음 항목을 포함하여 구현한다.

- OCR 원문과 confidence
- 중심 X·Y 좌표와 글자 높이
- 연결 가능한 약품 행 번호
- 후보 필드 종류와 미확인 사유
- 사용자 검토 상태
- 확인된 후보의 `ExtractedField` 전환

미확인 후보는 사용자 확인 전까지 자동 처방 확정이나 의약품 동일성
판단에 사용하지 않는다.

## 약품 행 판정

약품 행은 다음 근거를 함께 사용한다.

- 인식된 처방전 표 내부에 위치하는지
- 약품명 열에 위치하는지
- 정, 캡슐, 시럽, 연고, 주사액 등의 제형이 있는지
- mg, g, mL, % 등의 함량 표기가 있는지
- 투여량, 횟수, 기간 또는 복용 시점이 열 위치와 일치하는지

안내문 제외는 특정 문구만으로 결정하지 않는다. 표 위치와 약품명
근거가 부족한 일반 문장은 약품 행으로 생성하지 않는다.

## 여러 줄 약품명

- 직전 약품명과 수직 간격이 가까워야 한다.
- 연속 행의 값이 약품명 열에만 위치해야 한다.
- `90연질캡슐 1000mg`처럼 포장·제형·함량 형태를 만족해야 한다.
- `1정 복용`, `1정 드세요` 같은 행동 지시문은 병합하지 않는다.

## 제외된 행

- 안내문으로 판정된 행은 `ExtractedField`로 저장하지 않는다.
- 제외된 행의 감사·평가가 필요하면 원문, 좌표, 제외 사유를
  저장하는 별도 후보 행 모델을 도입해야 한다.
- 제외 행의 원문이 최종 처방 데이터로 자동 유입돼서는 안 된다.

## 평가 기준

CLOVA fixture 평가는 최소 다음 실패 유형을 포함한다.

1. 정상 약품 행 누락
2. 안내문을 약품으로 인식한 오탐
3. 한 약품의 필드가 다른 약품 index에 연결된 행 연결 오류
4. 명칭 헤더 누락
5. 좌우 이동 및 여백 크롭
6. 여러 줄 약품명과 안내문 병합 오류
7. 숫자 1의 `I`, `l`, `|` 오인식
