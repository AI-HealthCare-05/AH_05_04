# 데이터 구조 및 ERD

## 목적

테이블, 관계, 상태값과 주요 데이터 제약조건을 기록합니다.

## 현재 물리 DB 구성

현재 Backend의 물리 DB는 PostgreSQL 17입니다.

- SQLAlchemy 비동기 드라이버: `postgresql+asyncpg`
- Alembic 스키마 관리 대상: PostgreSQL
- Docker Compose 서비스명: `postgres`
- 애플리케이션 컨테이너 내부 포트: `5432`
- 로컬 공개 포트 기본값: `5432`

이번 전환은 물리 DB 엔진 교체이며 API 계약과 논리적 데이터 모델은 유지합니다.

UUID는 PostgreSQL native `UUID` 타입으로 변경하지 않고 기존 데이터 및 API 호환성을 위해 `CHAR(36)` 문자열로 저장합니다. Python 코드에서는 공통 `UUIDChar` 타입을 통해 `UUID` 객체와 DB 문자열 사이를 변환합니다. PostgreSQL native `UUID` 전환은 별도 migration 범위입니다.

`DateTime(timezone=True)` 필드는 PostgreSQL에서 시간대가 포함된 timestamp로 관리합니다. 애플리케이션의 컬럼별 UTC 또는 `Asia/Seoul` 시간대 정책은 기존 API·모델 계약과 동일하게 유지합니다.

물리 DB를 PostgreSQL로 전환하더라도 기본키, 외래키, Enum, 상태값과 nullable 의미 등 외부에서 관찰되는 논리적 계약은 변경하지 않습니다.

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

## 사용자

`user` 테이블은 MVP 인증과 내 정보 조회에 사용합니다.

| 컬럼 | 타입 | Nullable | 설명                                                                         |
|---|---|---:|------------------------------------------------------------------------------|
| `email` | `VARCHAR(40)` | No | 소문자로 정규화해 저장하는 로그인 이메일. 저장된 소문자 값을 기준으로 unique |
| `hashed_password` | `VARCHAR(128)` | No | 해시된 비밀번호                                                              |
| `name` | `VARCHAR(20)` | No | 사용자 이름                                                                  |
| `gender` | `ENUM('MALE', 'FEMALE')` | Yes | Post-MVP 추가 정보 입력 대상                                                 |
| `birthday` | `DATE` | Yes | Post-MVP 추가 정보 입력 대상                                                 |
| `phone_number` | `VARCHAR(20)` | Yes | Post-MVP 추가 정보 입력 대상. unique                                         |

MVP 회원가입 요청은 `name`, `email`, `password`만 받습니다. 가입 직후 `gender`, `birthday`, `phone_number`는 `null`일 수 있습니다.

이메일은 회원가입, 로그인 및 내 정보 수정 시 Backend에서 소문자로 정규화합니다. DB에는 정규화된 값만 저장하며, 조회 API도 저장된 소문자 값을 반환합니다. 이메일 unique와 중복 판정 역시 정규화된 값을 기준으로 적용하므로 대소문자만 다른 이메일은 동일하게 취급합니다.

## OCR 작업

`ocr_job` 테이블은 OCR 처리 상태와 오류 정보를 저장합니다.

| 컬럼 | 타입 | Nullable | 설명 |
|---|---|---:|---|
| `created_sequence` | `BIGINT UNSIGNED` | No | 같은 `created_at` 안에서 최신 작업을 안정적으로 정렬하기 위한 생성 순서 기준 |
| `error_code` | `VARCHAR(100)` | Yes | 실패 상태의 안전한 오류 코드 |
| `error_message` | `VARCHAR(500)` | Yes | 실패 상태 조회 응답에 포함할 수 있는 안전한 사용자 안내 문구 |

`idx_ocr_document_created`는 기존 FK 지원 인덱스로 유지하고, 최신 작업 정렬용 `idx_ocr_document_created_seq(document_id, created_at, created_sequence)`를 별도로 사용합니다.

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

## Post-MVP-1 목표 스키마 — 미구현

Contract Freeze v1은 다음 구조를 목표로 승인했습니다. 아래 테이블과 제약은 현재 migration·모델에 구현된 것으로 간주하지 않으며, 실제 도입 시 expand → backfill → 검증 → read cutover → contract 순서와 rollback 계획을 별도 migration PR에서 확정합니다.

| 영역 | 목표 테이블 | 목표 제약 |
| --- | --- | --- |
| 비동기 실행 | `ai_job`, `outbox_event`, 비동기 `idempotency_record`, 동기 `sync_idempotency_record` | Job 6상태, at-least-once, DB commit 후 ACK, 두 재응답 방식 분리 |
| 처방 버전 | `prescription_version`, `prescription_version_medication` | 불변 snapshot과 처방별 단일 active version |
| 복약 기록 | `medication_schedule`, `medication_occurrence`, `medication_checkin`, audit | Check-in 3결과, occurrence별 단일 현재 결과, 정정 이력 보존 |
| Barrier·Support | `safety_assessment`, `barrier_response`, `support_action_plan`, follow-up | Safety 우선, 거절과 미제출 구분, revision별 무효화 |
| AI 안전 결과 | retrieval·result·citation 계열 | Job·처방 version 귀속, 생성·검증·공개 상태축 분리 |
| OTC | 제품·성분·rule·평가 계열 | 평가 당시 rule·source version snapshot과 fail-closed 결과 |

상세 목표는 [계약 인덱스](./contracts/README.md)의 v1 문서를 따릅니다. 목표 enum·컬럼을 현재 코드가 이미 사용한다고 설명하지 않습니다.
