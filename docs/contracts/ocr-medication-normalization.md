# OCR 약품명 정규화 계약

## 관련 요구사항

- REQ-CLN-009: 원문값과 정규화값 분리
- REQ-CLN-014: OCR 약품명 표기 정규화

## 목적

OCR로 추출한 약품명의 원문을 보존하면서 공백, 괄호, 단위 표기를
일관된 형식으로 정리한다.

공식 의약품명 검색, OCR 오타 교정, 제품 후보 매칭은 이번 범위에
포함하지 않는다.

## 필드 정의

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| raw_value | string 또는 null | 선택 | OCR가 인식한 원문 |
| normalized_value | string 또는 null | 선택 | 규칙 기반으로 표기를 정리한 값 |
| confirmed_value | string 또는 null | 선택 | 사용자가 최종 확인하거나 수정한 값 |
| normalization_version | string 또는 null | 선택 | 적용한 정규화 규칙 버전 |

## 처리 원칙

- `raw_value`는 수정하거나 덮어쓰지 않는다.
- 정규화는 `MEDICATION_NAME` 필드에만 적용한다.
- 여러 공백은 하나로 합친다.
- 함량 값과 단위 사이의 공백을 제거한다.
- `MG`, `mg`, `㎎` 등의 단위 표기를 통일한다.
- 괄호로 둘러싸인 함량은 일반 공백 형식으로 변환한다.
- OCR 오타로 추정되는 글자는 임의로 교정하지 않는다.
- 공식 제품명 매칭은 별도 후속 기능으로 처리한다.

## 예시

입력:

```json
{
  "field_type": "MEDICATION_NAME",
  "raw_value": "로수바스타틴칼숨정  10 mg"
}
```
출력:

``` json
{
  "field_type": "MEDICATION_NAME",
  "raw_value": "로수바스타틴칼숨정  10 mg",
  "normalized_value": "로수바스타틴칼숨정 10mg",
  "confirmed_value": null,
  "normalization_version": "rule-v1"
}
```

이미지에 `칼숨`이라고 적혀 있으므로 시스템은 이를 `칼슘`으로 자동 교정하지 않는다.
그리고 `docs/contracts/README.md`의 계약 문서 목록에 추가합니다.

```markdown
- [OCR 약품명 정규화 계약](./ocr-medication-normalization.md)
```
