# OCR 약품명 정규화 계약

## 관련 요구사항

- REQ-CLN-009: 원문값과 정규화값 분리
- REQ-CLN-014: OCR 약품명 표기 정규화

## 목적

OCR로 추출한 약품명의 원문을 보존하면서 공백, 괄호 및 단위 표기를
일관된 형식으로 정리한다.

공식 의약품명 검색, OCR 오타 교정, 제품 후보 매칭은 이번 범위에
포함하지 않는다.

## 필드 정의

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| raw_value | string 또는 null | 선택 | OCR이 인식한 원문 |
| normalized_value | string 또는 null | 선택 | 원문의 표기만 정리한 참고값 |
| confirmed_value | string 또는 null | 선택 | 사용자가 확인하거나 수정한 최종 기준값 |
| normalization_version | string 또는 null | 선택 | 적용한 정규화 규칙 버전 |

## 값의 역할과 사용 범위

- `raw_value`는 OCR이 인식한 원문이며 수정하거나 덮어쓰지 않는다.
- `normalized_value`는 원문의 위치와 순서를 유지하면서 공백과 단위 표기만 정리한 참고값이다.
- `confirmed_value`는 사용자가 확인하거나 수정한 최종 기준값이다.
- `normalized_value`는 자동 처방 확정에 사용하지 않는다.
- `normalized_value`만으로 의약품 동일성을 판단하지 않는다.
- 최종 처방에는 사용자가 확인한 `confirmed_value`만 사용한다.
- `confirmed_value`가 없으면 사용자 확인이 완료되지 않은 상태로 취급한다.

## 처리 원칙

- 정규화는 `MEDICATION_NAME` 필드에만 적용한다.
- 여러 공백은 하나로 합친다.
- 함량 값과 단위 사이의 공백을 제거한다.
- 복합 함량과 농도 표기의 슬래시 주변 공백을 제거한다.
- `MG`, `mg`, `㎎` 등의 단위 표기를 통일한다.
- 괄호로 둘러싸인 함량은 일반 공백 형식으로 변환한다.
- 약품명, 성분명 및 함량의 위치나 순서를 변경하지 않는다.
- 정규화를 반복해도 결과가 변경되지 않아야 한다.
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
```json
{
  "field_type": "MEDICATION_NAME",
  "raw_value": "로수바스타틴칼숨정  10 mg",
  "normalized_value": "로수바스타틴칼숨정 10mg",
  "confirmed_value": null,
  "normalization_version": "rule-v1"
}
```
이미지에 칼숨이라고 적혀 있으므로 시스템은 이를 칼슘으로 자동 교정하지 않는다.

복합 함량:
``` text
복합정 500 mg / 5 mg
→ 복합정 500mg/5mg
```

농도:
``` text
주사액 1 mg / mL
→ 주사액 1mg/mL
```

퍼센트:
``` text
연고 5 %
→ 연고 5%
```
