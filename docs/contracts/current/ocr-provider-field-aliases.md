# OCR Provider 약품명 필드 Alias 예방 계약

> Status: Current Preventive Contract · No active external alias
> 관련 항목: Issue #153 CD-001
> 적용 범위: OCR Provider Adapter 입력 경계와 이후 내부 데이터 계약

## 1. 목적

외부 OCR Provider 또는 외부 연동 시스템이 약품명을 저장소의 정본과 다른 필드명으로 전달할 경우, 해당 이름이 Backend DTO, 공개 API, DB, Frontend 또는 이벤트 계약으로 확산되는 것을 방지합니다.

현재 저장소에는 `drugName`, `medicine_name`을 실제 입력으로 사용하는 Provider Source나 이를 변환하는 Adapter 구현이 없습니다.

따라서 이 문서는 현재 존재하는 alias 호환 동작을 설명하는 문서가 아니라, 향후 외부 alias가 도입될 때 반드시 따라야 하는 예방적 경계 규칙을 정의합니다.

## 2. 현재 정본

약품명을 표현하는 현재 정본은 다음과 같습니다.

| 영역 | 정본 | 설명 |
| --- | --- | --- |
| Backend 공개 DTO | `medication_name` | 공개 API에서 사용하는 약품명 필드 |
| Backend 내부 DTO | `medication_name` | 서비스 계층에서 사용하는 약품명 필드 |
| DB | `medication_name` | 사용자가 확인한 처방 약품명 저장 필드 |
| Frontend API type | `medication_name` | Backend 응답을 소비하는 필드 |
| OCR FieldType | `MEDICATION_NAME` | OCR 추출 필드의 의미를 나타내는 enum |
| Stream·Event payload | `medication_name` | 약품명을 전달해야 하는 경우 사용하는 정본 |

OCR Provider Adapter 이후의 내부 계층에서는 위 정본만 사용합니다.

## 3. 현재 외부 Alias 상태

현재 활성화되거나 허용된 외부 약품명 alias는 없습니다.

| 입력 필드 | Source | 현재 상태 | 내부 변환 |
| --- | --- | --- | --- |
| `medication_name` | 내부 Backend·API 계약 | 허용 | 변환 없음 |
| `MEDICATION_NAME` | OCR FieldType | 허용 | 의미상 `medication_name`으로 구조화 |
| `drugName` | 확인된 Source 없음 | 허용하지 않음 | 없음 |
| `medicine_name` | 확인된 Source 없음 | 허용하지 않음 | 없음 |

`drugName`, `medicine_name`은 현재 호환을 보장하는 입력 필드가 아닙니다.

해당 문자열이 문서에 기록돼 있다는 이유만으로 Provider Adapter, Backend 요청 DTO 또는 공개 API에서 이를 자동 수용해서는 안 됩니다.

## 4. Alias 허용 경계

향후 외부 Provider가 별도의 약품명 필드를 실제로 제공하는 경우 alias 변환은 해당 Provider Adapter의 입력 경계에서만 수행합니다.

```text
외부 Provider payload
        ↓
Source 전용 Provider Adapter
        ↓ alias → 정본 변환
medication_name 또는 MEDICATION_NAME
        ↓
Backend DTO·DB·Frontend·Event
```

다음 계층에서는 외부 alias를 허용하지 않습니다.

- Backend 공개 요청·응답 DTO
- Backend 내부 서비스 DTO
- DB column
- Frontend API type
- Stream·Event payload
- Guide·Chat·RAG 입력 DTO
- 외부로 반환하는 API 응답

Provider Adapter는 외부 필드명을 내부 정본으로 변환한 뒤 외부 alias를 제거해야 합니다.

## 5. 신규 Alias 등록 조건

신규 alias는 다음 조건을 모두 충족한 경우에만 등록할 수 있습니다.

1. 실제 외부 Source와 payload가 확인되어야 합니다.
2. Source 이름과 버전 또는 Template 식별자가 기록되어야 합니다.
3. alias가 표현하는 의미가 약품명인지 확인되어야 합니다.
4. 제품 함량, 성분명, 제품 식별자 등 다른 의미의 필드를 약품명 alias로 등록해서는 안 됩니다.
5. Source 전용 Provider Adapter에 명시적인 매핑을 추가해야 합니다.
6. 합성 fixture를 사용한 매핑 테스트를 추가해야 합니다.
7. Adapter 이후 계층에 alias가 노출되지 않는 계약 테스트를 추가해야 합니다.
8. Backend·Frontend·OCR 계약 검토를 받아야 합니다.
9. 실제 환자정보, 처방전 원본 또는 Provider 인증정보를 fixture에 포함해서는 안 됩니다.

등록되는 alias는 전역 추론 규칙이 아니라 Source별 허용 목록으로 관리합니다.

## 6. Source별 매핑표

현재 등록된 외부 Source alias는 없습니다.

향후 alias가 승인되면 다음 형식으로 이 표에 추가합니다.

| Source | Source version 또는 Template ID | 외부 입력 필드 | 내부 정본 | 도입 PR | 상태 |
| --- | --- | --- | --- | --- | --- |
| 등록된 Source 없음 | — | — | `medication_name` | — | 비활성 |

Source가 확인되지 않은 일반적인 이름을 추측하여 이 표에 추가하지 않습니다.

## 7. 충돌 및 누락 처리

별도의 Source별 계약이 없는 경우 다음 기본 규칙을 적용합니다.

### 7.1 미등록 Alias

등록되지 않은 외부 필드명을 약품명으로 자동 추론하지 않습니다.

예를 들어 `drug`, `drug_name`, `drugName`, `medicine`, `medicine_name`, `product_name` 등의 이름을 문자열 유사성만으로 `medication_name`에 매핑해서는 안 됩니다.

### 7.2 정본과 Alias 동시 입력

정본과 alias가 동시에 전달되는 입력 형식은 Source별 계약에서 명시적으로 정의하지 않는 한 허용하지 않습니다.

두 값 중 하나를 임의로 우선하거나 조용히 덮어써서는 안 됩니다.

### 7.3 복수 Alias 입력

서로 다른 alias가 동시에 전달되면 임의로 하나를 선택하지 않습니다.

Source 계약에 정의된 오류 또는 검수 필요 상태로 처리해야 합니다.

### 7.4 빈 값

빈 문자열, 공백만 있는 문자열 또는 `null`을 유효한 약품명으로 변환해서는 안 됩니다.

해당 값은 누락 또는 검수 필요 상태로 처리합니다.

### 7.5 의미가 다른 필드

다음 값은 `medication_name` alias로 취급하지 않습니다.

- 공식 제품 식별자의 제품명
- MFDS Catalog의 공식 제품명
- 성분명 전용 필드
- 제품 함량
- 1회 복용량
- Provider 내부 문서 제목
- Template 표시용 label

처방전에서 사용자가 확인한 `medication_name`과 향후 Identification 또는 Catalog의 공식 제품 정보는 별도 개념으로 유지합니다.

## 8. Template OCR 필드

Template OCR의 사용자 정의 필드명은 자동으로 공개 API 필드가 되지 않습니다.

예를 들어 Template에서 다음과 같은 필드명을 사용할 수 있습니다.

```text
med_1_name
medication_1
prescription_drug_1
```

이러한 이름은 해당 Template Adapter 내부에서만 해석하며, Adapter 출력은 다음 중 승인된 내부 형식으로 변환해야 합니다.

```text
FieldType.MEDICATION_NAME
```

또는

```text
medication_name
```

Template 필드명을 Backend DTO, DB 또는 Frontend type으로 그대로 전달해서는 안 됩니다.

## 9. 공개 계약 비노출 원칙

외부 alias가 Provider Adapter에서 지원되더라도 공개 API 소비자는 해당 alias를 알 필요가 없어야 합니다.

다음과 같은 공개 응답은 허용하지 않습니다.

```json
{
  "drugName": "합성약정"
}
```

```json
{
  "medicine_name": "합성약정"
}
```

공개 응답은 정본만 사용합니다.

```json
{
  "medication_name": "합성약정"
}
```

OCR 추출 필드에서는 승인된 FieldType을 사용합니다.

```json
{
  "field_type": "MEDICATION_NAME",
  "raw_value": "합성약정"
}
```

## 10. 계약 테스트 기준

현재 외부 alias가 없으므로 현재 단계의 계약 테스트는 다음 불변 조건을 검증합니다.

- Backend 처방 DTO에 `medication_name`이 존재합니다.
- OCR 모델에 `FieldType.MEDICATION_NAME`이 존재합니다.
- 공개 Backend DTO에 `drugName`이 존재하지 않습니다.
- 공개 Backend DTO에 `medicine_name`이 존재하지 않습니다.
- 이 문서가 계약 문서 인덱스에 등록되어 있습니다.
- 현재 등록된 외부 Source alias가 없다는 상태가 문서에 명시되어 있습니다.

향후 실제 alias가 추가되는 PR은 다음 테스트를 함께 추가해야 합니다.

- 승인된 Source payload의 alias가 `medication_name`으로 변환됩니다.
- 미등록 alias가 자동 변환되지 않습니다.
- 빈 값이 유효한 약품명으로 변환되지 않습니다.
- 충돌하는 정본과 alias를 임의로 선택하지 않습니다.
- Adapter 출력에 외부 alias가 남지 않습니다.
- Backend DTO·DB·Frontend·Event에는 정본만 전달됩니다.
- 실제 환자정보가 없는 합성 fixture로 검증됩니다.

## 11. 범위 제외

이 계약은 다음 내용을 현재 구현하거나 확정하지 않습니다.

- `drugName` 지원
- `medicine_name` 지원
- Source가 확인되지 않은 alias 자동 추론
- 외부 Provider 선택
- Template OCR Template ID 확정
- MFDS 공식 제품 DTO의 필드명 확정
- 처방 약품명을 공식 제품명으로 자동 덮어쓰기
- Frontend alias fallback
- DB migration

실제 외부 alias가 도입되면 별도 구현 Issue와 PR에서 Source, 매핑, 오류 처리 및 테스트를 확정합니다.

## 12. 변경 관리

이 문서를 변경하는 PR에는 다음 내용을 포함해야 합니다.

- 관련 Source와 payload 근거
- 추가·삭제되는 alias
- 영향을 받는 Provider Adapter
- 충돌·누락 처리 방식
- 합성 fixture 및 계약 테스트
- Backend·Frontend·OCR 영향 검토
- 공개 DTO·DB·Event 정본 유지 여부

실제 외부 alias가 추가되기 전까지 Issue #153 CD-001은 runtime blocker가 아닌 비차단 예방 Ledger로 유지합니다.

## 13. 관련 문서

- `docs/api.md`
- `docs/data-schema.md`
- `docs/contracts/current/ocr-medication-normalization.md`
- `docs/contracts/README.md`
- Issue #153 CD-001
