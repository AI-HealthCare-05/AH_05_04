# 데이터 구조 및 ERD

## 목적

테이블, 관계, 상태값과 주요 데이터 제약조건을 기록합니다.

## 현재 구현 테이블

| 영역 | 테이블 | 현재 사용 상태 |
| --- | --- | --- |
| 사용자 | `user` | 인증·사용자 정보에 사용 |
| 의료문서 | `medical_document` | 처방전 metadata와 로컬 파일 object key 저장 |
| OCR | `ocr_job`, `extracted_field` | 동기 OCR 상태, 원문·정규화·사용자 확정값 저장 |
| 처방 | `prescription`, `medication` | 사용자 확정 처방과 약물 저장 |
| 가이드 | `guide` | 동기 생성 상태·본문·모델·프롬프트 버전 저장 |
| 채팅 | `chat_session`, `chat_message` | 세션과 USER·ASSISTANT 메시지, 생성 상태 저장 |
| 의료 지식 | `knowledge_document`, `knowledge_chunk` | Schema-only Post-MVP 골격, 현재 검색 경로에서 미사용 |
| 인용 | `guide_citation`, `chat_citation` | Schema-only Post-MVP 골격, 현재 생성·API 경로에서 미사용 |

멀티 프로필, 환자·보호자 권한, 복약 일정·기록과 감사 로그는 현재 DB 모델·migration에 구현되어 있지 않은 후속 범위입니다.

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

## 생성 상태

- `guide.generation_status`: `PENDING | GENERATING | COMPLETED | FAILED`
- USER `chat_message.generation_status`: `NOT_APPLICABLE`
- ASSISTANT `chat_message.generation_status`: `PENDING | GENERATING | COMPLETED | FAILED`
- 같은 채팅 세션의 `message_seq`는 중복될 수 없습니다.

## Post-MVP schema-only 테이블

`knowledge_document`, `knowledge_chunk`, `guide_citation`, `chat_citation`은 migration과 SQLAlchemy 모델에는 존재하지만 현재 repository, service, API DTO와 응답에는 연결되지 않습니다. 테이블 존재를 RAG, 출처 인용 또는 Citation/NLI 검증 구현 완료로 해석하지 않습니다.
