# 데이터 구조 및 ERD

## 목적

테이블, 관계, 상태값과 주요 데이터 제약조건을 기록합니다.

## 주요 영역

- 사용자 및 멀티 프로필
- 환자·보호자 권한
- 처방 및 약물 정보
- 복약 일정과 기록
- 파일 및 OCR 결과
- 감사 로그

## 변경 원칙

DB 모델 또는 마이그레이션 변경 시 이 문서와 API 영향을 함께 갱신합니다.


## OCR 추출 필드

`extracted_field` 테이블은 OCR이 추출한 필드와 사용자의 확인 결과를 저장합니다.

| 컬럼 | 타입 | Nullable | 설명 |
|---|---|---:|---|
| `raw_value` | `VARCHAR(1000)` | Yes | OCR이 인식한 원문 |
| `normalized_value` | `VARCHAR(1000)` | Yes | 원문의 표기만 정리한 참고값 |
| `confirmed_value` | `VARCHAR(1000)` | Yes | 사용자가 확인하거나 수정한 최종 기준값 |
| `normalization_version` | `VARCHAR(30)` | Yes | 적용한 정규화 규칙 버전 |

- `normalized_value`와 `normalization_version`은 `MEDICATION_NAME` 필드에만 저장한다.
- 사용자 확인 전에는 `confirmed_value`가 `null`이다.
- 사용자 확인 전 `confirmation_status`는 `UNCONFIRMED`이다.
- 최종 처방에는 사용자가 확인한 `confirmed_value`만 사용한다.
