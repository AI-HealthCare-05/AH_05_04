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
| 프로필 | `profile` | 본인 단일 `SELF` profile과 사용자 리소스 소유권 기준에 사용 |
| 의료문서 | `medical_document` | 처방전 metadata와 로컬 파일 object key 저장 |
| OCR | `ocr_job`, `extracted_field` | 동기 OCR 상태, 원문·정규화·사용자 확정값 저장 |
| 처방 | `prescription`, `medication` | 사용자 확정 처방과 약물 저장 |
| 가이드 | `guide` | 동기 생성 상태·본문·모델·프롬프트 버전 저장 |
| 채팅 | `chat_session`, `chat_message` | 세션과 USER·ASSISTANT 메시지, 생성 상태 저장 |
| 의료 지식 | `knowledge_document`, `knowledge_chunk` | Schema-only Post-MVP 골격, 현재 검색 경로에서 미사용 |
| 인용 | `guide_citation`, `chat_citation` | Schema-only Post-MVP 골격, 현재 생성·API 경로에서 미사용 |
| 비동기 실행 | `ai_job`, `outbox_event`, `idempotency_record` | `JobIntakeService`(#147)의 Job 접수 transaction과 DB Outbox 선점·`WorkerMessage` 조립·Redis 발행·fencing 완료(#219)가 repository·service 계층에 연결됨. 실제 OCR·Guide·Chat API DTO·응답 경로는 아직 미연결(#148) |
| 비동기 실행(schema-only) | `ai_job_attempt`, `message_quarantine`, `dlq_outbox_event` | Schema-only Post-MVP 골격, 현재 repository·service·API 경로에서 미사용 |
| RAG Source·Catalog | `rag_source`, `rag_source_endpoint`, `rag_source_operation`, `rag_source_snapshot`, `rag_source_ingestion_run`, `rag_source_ingestion_artifact`, `rag_source_snapshot_verification`, `rag_medication_product`, `rag_medication_ingredient`, `rag_medication_alias`, `rag_medication_product_component` | #164 최소 DB 기반과 #165 원본 Artifact 참조. 공식 Source 승인·Catalog 적재·RAG 검색·Runtime 활성화는 후속 범위 |

본인 단일 `SELF` profile과 `profile_id` 기반 소유권 전환은 #117 구현 PR에서 도입했습니다. 보호자·멀티 프로필·위임 권한은 후속 범위이며, 현재 구현은 사용자 1명당 `SELF` profile 1개만 허용합니다. 복약 일정·기록과 감사 로그는 아직 목표 계약과 현재 구현을 구분합니다.

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
| `is_active` | `BOOLEAN` | No | 로그인 가능 여부. `account_status`가 `ACTIVE`가 아니면 함께 `false` |
| `account_status` | `VARCHAR(25)` | No | 계정 상태. `ACTIVE`, `WITHDRAWAL_REQUESTED`, `WITHDRAWN` |
| `withdrawal_requested_at` | timezone datetime | Yes | 회원탈퇴 요청 시각 |
| `withdrawn_at` | timezone datetime | Yes | 회원탈퇴 완료 시각 |
| `token_version` | `INTEGER` | No | 로그아웃·비밀번호 재설정·회원탈퇴 시 증가하는 세션 무효화 카운터. 기본값 `0` |

MVP 회원가입 요청은 `name`, `email`, `password`만 받습니다. 가입 직후 `gender`, `birthday`, `phone_number`는 `null`일 수 있습니다.

이메일은 회원가입, 로그인 및 내 정보 수정 시 Backend에서 소문자로 정규화합니다. DB에는 정규화된 값만 저장하며, 조회 API도 저장된 소문자 값을 반환합니다. 이메일 unique와 중복 판정 역시 정규화된 값을 기준으로 적용하므로 대소문자만 다른 이메일은 동일하게 취급합니다.

access token과 refresh token에는 발급 시점의 `token_version`을 포함합니다. 인증된 요청과 `GET /api/v1/auth/token/refresh`는 DB의 `user.token_version`, `account_status`, `is_active`를 다시 확인하며, 로그아웃은 `token_version`을 DB에서 원자적으로 `+1`하고 `refresh_token` 쿠키를 삭제합니다.

## PROFILE SELF 소유권

`profile` 테이블은 본인 단일 `SELF` profile을 저장합니다.

| 컬럼 | 타입 | Nullable | 설명 |
| --- | --- | ---: | --- |
| `id` | `CHAR(36)` | No | Profile PK |
| `user_id` | `CHAR(36)` | No | `user.id` FK |
| `profile_type` | `VARCHAR(30)` | No | 현재는 `SELF`만 허용 |
| `display_name` | `VARCHAR(100)` | No | 기본 표시명 |
| `created_at` | timezone datetime | No | 생성 시각 |
| `updated_at` | timezone datetime | No | 수정 시각 |

DB 제약:

- `(user_id, profile_type)` unique
- `profile_type = 'SELF'` CHECK

회원가입 경로는 사용자 생성과 함께 SELF profile을 생성합니다. 기존 사용자처럼 SELF profile이 없을 수 있는 row가 신규 의료문서를 생성할 때는 `INSERT ... ON CONFLICT DO NOTHING RETURNING` 기준으로 SELF profile을 멱등 생성한 뒤 같은 transaction에서 리소스 `profile_id`로 사용합니다.

## 사용자 리소스 소유권 관계

사용자 의료 리소스는 `profile_id`를 기준으로 소유권을 확인합니다. 소유권이 없거나 리소스가 존재하지 않으면 존재 여부를 숨기기 위해 동일하게 `404`를 반환합니다.

| 테이블 | 소유권 기준 |
| --- | --- |
| `medical_document` | `medical_document.profile_id` |
| `ocr_job` | `ocr_job → medical_document → profile_id` |
| `prescription` | `prescription.profile_id` |
| `guide` | `guide.profile_id` |
| `chat_session` | `chat_session.profile_id` |
| `chat_message` | `chat_message → chat_session → profile_id` |

부모·자식 리소스의 `profile_id`가 달라지는 상태는 DB 제약으로 차단합니다.

| 관계 | 제약 |
| --- | --- |
| `medical_document` | `(id, profile_id)` unique |
| `prescription` | `(id, profile_id)` unique, `(document_id, profile_id) → medical_document(id, profile_id)` composite FK |
| `guide` | `(prescription_id, profile_id) → prescription(id, profile_id)` composite FK |
| `chat_session` | `(prescription_id, profile_id) → prescription(id, profile_id)` composite FK |

`ocr_job.profile_id`와 `chat_message.profile_id`는 직접 저장하지 않습니다. OCR 작업은 의료문서에 종속되고, Chat message는 세션에 종속되므로 부모 chain의 `profile_id`를 기준으로 확인합니다.

## OCR 작업

`ocr_job` 테이블은 OCR 처리 상태와 오류 정보를 저장합니다.

| 컬럼 | 타입           | Nullable | 설명 |
|---|----------------|---:|---|
| `created_sequence` | `BIGINT`       | No | 같은 `created_at` 안에서 최신 작업을 안정적으로 정렬하기 위한 생성 순서 기준 |
| `ai_job_id` | `CHAR(36)` | Yes | `ai_job.id` nullable FK. AI Job 삭제 시 `NULL`로 전환되며 하나의 AI Job은 최대 하나의 OCR 작업에만 연결 |
| `error_code` | `VARCHAR(100)` | Yes | 실패 상태의 안전한 오류 코드 |
| `error_message` | `VARCHAR(500)` | Yes | 실패 상태 조회 응답에 포함할 수 있는 안전한 사용자 안내 문구 |
| `engine_name` | `VARCHAR(100)` | Yes | 실제 OCR 실행 엔진 식별자 |
| `model_version` | `VARCHAR(100)` | Yes | OCR 구조화에 사용한 실제 모델 ID |
| `prompt_version` | `VARCHAR(100)` | Yes | OCR 구조화 프롬프트 버전 |

`idx_ocr_document_created`는 기존 FK 지원 인덱스로 유지하고, 최신 작업 정렬용 `idx_ocr_document_created_seq(document_id, created_at, created_sequence)`를 별도로 사용합니다.

- 성공한 신규 OCR 작업에는 실제 `engine_name`을 기록합니다.
- LLM 구조화가 실제 실행된 경우에만 `model_version`과 `prompt_version`을 기록합니다.
- 규칙 기반 구조화 경로에서는 `model_version`과 `prompt_version`이 `null`입니다.
- 기존 작업이나 구조화 단계 이전에 실패한 작업에서는 실행 metadata가 `null`일 수 있습니다.
- Provider 원문 응답, 처방전 원문 또는 API Key는 실행 metadata에 저장하지 않습니다.

`ocr_job.ai_job_id`는 공통 비동기 AI Job과 OCR 결과를 연결하기 위한 nullable FK입니다.

- FK: `fk_ocr_job_ai_job`
- 참조 대상: `ai_job.id`
- 삭제 동작: `ON DELETE SET NULL`
- unique 제약: `uq_ocr_job_ai_job`
- 기존 OCR 행: `ai_job_id=NULL` 유지
- 기존 행을 위한 synthetic AI Job이나 backfill은 생성하지 않음
- 신규 비동기 OCR 접수에서 실제 값을 연결하는 서비스 로직은 #148 범위

FK와 unique 제약은 존재하는 AI Job 참조와 OCR 영역 내부의 일대일 연결을 DB에서 보장합니다. `job_type='OCR'` 검증과 OCR·Guide·Chat 전체 영역에서 하나의 결과 row만 연결되도록 하는 검증은 #148의 Job 접수 서비스가 담당합니다.

OCR 결과 소유권은 `ai_job_id`만으로 판단하지 않고 기존 `ocr_job → medical_document → profile_id` 경로로 확인합니다.

## OCR 추출 필드

`extracted_field` 테이블은 OCR이 추출한 필드와 사용자의 확인 결과를 저장합니다.

| 컬럼 | 타입 | Nullable | 설명 |
|---|---|---:|---|
| `raw_value` | `VARCHAR(1000)` | Yes | OCR이 인식한 원문 |
| `normalized_value` | `VARCHAR(1000)` | Yes | 원문의 표기만 정리한 참고값 |
| `confirmed_value` | `VARCHAR(1000)` | Yes | 사용자가 확인하거나 수정한 최종 기준값 |
| `normalization_version` | `VARCHAR(30)` | Yes | 적용한 정규화 규칙 버전 |
| `field_type` | `VARCHAR(30)` | No | OCR 필드 종류. `MEDICATION_STRENGTH`를 포함 |

- `MEDICATION_NAME`에는 약품명 표기 정규화를 적용하며 `normalization_version`은 `rule-v1`입니다.
- LLM 경로의 `PRESCRIBED_DATE`에는 `YYYY-MM-DD` 정규화를 적용하며 `normalization_version`은 `date-rule-v1`입니다.
- `MEDICATION_STRENGTH`를 포함한 그 밖의 필드는 현재 `normalized_value`와 `normalization_version`을 생성하지 않습니다.
- OCR 원문이 없는 사용자 입력용 빈 검수 필드는 `raw_value`, `normalized_value`, `normalization_version`이 모두 `null`일 수 있습니다.
- 사용자 확인 전에는 `confirmed_value`가 `null`이다.
- 사용자 확인 전 `confirmation_status`는 `UNCONFIRMED`이다.
- 최종 처방에는 사용자가 확인한 `confirmed_value`만 사용한다.
- `MEDICATION_STRENGTH`는 제품 함량을 표현하며 `DOSE_VALUE`·`DOSE_UNIT`과 구분합니다.
- 제품 함량은 `100mg`, `5mg/100mg`, `1mg/mL`, `500mg/5mL`과 같은 문자열을 보존합니다.
- 확인되지 않은 제품 함량은 최종 처방에 저장하지 않습니다.

## 확정 처방 약물

`medication` 테이블은 사용자가 확인한 약물별 확정값을 저장합니다.

| 컬럼 | 타입 | Nullable | 설명 |
| --- | --- | ---: | --- |
| `medication_name` | `VARCHAR(255)` | No | 사용자가 확인한 약물명 또는 성분명 |
| `strength_text` | `VARCHAR(100)` | Yes | 처방전에 기재된 제품 함량 |
| `dose_value` | `NUMERIC(10,3)` | Yes | 실제 1회 복용량 |
| `dose_unit` | `VARCHAR(50)` | Yes | 실제 1회 복용 단위 |
| `frequency_per_day` | `INTEGER` | Yes | 하루 복용 횟수 |
| `timing_text` | `VARCHAR(255)` | Yes | 복용 시점 |
| `duration_days` | `INTEGER` | Yes | 복용 기간 |
| `display_order` | `INTEGER` | No | 처방전상의 약물 표시 순서 |

- `strength_text`는 `dose_value`·`dose_unit`과 의미가 다른 선택값입니다.
- 확인된 `MEDICATION_STRENGTH`가 있으면 `strength_text`에 저장합니다.
- 제품 함량이 없는 처방전도 확정할 수 있습니다.

## 제품 함량 Migration rollback 정책

Revision `529b2a36b677`은 다음 schema를 추가합니다.

- `extracted_field.field_type`의 `MEDICATION_STRENGTH`
- `medication.strength_text`
- `ocr_job.prompt_version`

Production에서는 해당 revision을 downgrade하지 않고 후속 migration으로 forward-fix합니다.

비운영 환경에서 downgrade하려면 위 필드에 저장된 데이터가 없어야 합니다. 데이터가 하나라도 존재하면 migration은 constraint 또는 컬럼을 변경하기 전에 중단됩니다. 데이터 삭제나 변환이 필요하면 백업·영향 확인 및 승인된 rollback 절차를 먼저 수행해야 합니다.

## OCR–AI Job Mapping Migration rollback 정책

Revision `c3f8a12d9e47`은 `ocr_job.ai_job_id` nullable FK와 `uq_ocr_job_ai_job` unique 제약을 추가합니다.

기존 OCR 행은 `ai_job_id=NULL`로 유지하며 synthetic AI Job 생성이나 backfill을 수행하지 않습니다. `ai_job` 삭제 시 OCR 결과 행은 보존되고 `ai_job_id`만 `NULL`로 전환됩니다.

Production에서는 연결 정보를 제거하는 downgrade 대신 forward-fix를 사용합니다. 비운영 환경에서도 `ocr_job.ai_job_id IS NOT NULL`인 행이 하나라도 존재하면 migration은 제약이나 컬럼을 제거하기 전에 downgrade를 중단합니다. downgrade가 필요하면 승인된 절차에 따라 연결 정보를 백업하거나 정리한 뒤 non-null 행이 0건인지 다시 검증해야 합니다.

## Guide–AI Job Mapping Migration rollback 정책

Revision `20fd11d29ecc`는 OCR과 같은 목적으로 `guide.ai_job_id` nullable FK와 `uq_guide_ai_job` unique 제약을 추가합니다.

- FK: `fk_guide_ai_job`
- 참조 대상: `ai_job.id`
- 삭제 동작: `ON DELETE SET NULL`
- unique 제약: `uq_guide_ai_job`
- 기존 Guide 행: `ai_job_id=NULL` 유지
- 기존 행을 위한 synthetic AI Job이나 backfill은 생성하지 않음

Outbox는 30일, Job은 90일 보존이므로 이 컬럼 없이 Outbox 역조회(`get_interim_domain_reference`)에만 의존하면 31~90일 구간에서 Job이 살아있어도 rediscovery·`GET /jobs/{job_id}`가 `404`를 반환할 수 있습니다(#148 네 번째 리뷰 지적). `JobStatusService`는 `guide.ai_job_id`가 채워진 뒤에는 이 값을 Outbox 역조회보다 우선 사용합니다.

Production에서는 연결 정보를 제거하는 downgrade 대신 forward-fix를 사용합니다. 비운영 환경에서도 `guide.ai_job_id IS NOT NULL`인 행이 하나라도 존재하면 migration은 제약이나 컬럼을 제거하기 전에 downgrade를 중단합니다. downgrade가 필요하면 승인된 절차에 따라 연결 정보를 백업하거나 정리한 뒤 non-null 행이 0건인지 다시 검증해야 합니다.

## 생성 상태

- `guide.generation_status`: `PENDING | GENERATING | COMPLETED | FAILED`
- USER `chat_message.generation_status`: `NOT_APPLICABLE`
- ASSISTANT `chat_message.generation_status`: `PENDING | GENERATING | COMPLETED | FAILED`
- 같은 채팅 세션의 `message_seq`는 중복될 수 없습니다.

## RAG Source·Catalog 최소 DB 기반

Revision `164f3a2b1c0d`는 #164의 후속 적재 준비를 위해 Source/Snapshot/Catalog 최소 DB 기반을 추가합니다. Revision `165a4b3c2d1e`는 수집 실행별 원본 Artifact 참조와 무결성 메타데이터를 추가하고, `165b5c4d3e2f`는 거부 원문의 안전한 추적 필드를 추가합니다.

이번 분할 범위의 ID/FK 매핑은 기존 애플리케이션 호환성을 우선해 `UUIDChar` 기반 `CHAR(36)`을 사용합니다. 신규 독립 RAG/Eval ID의 PostgreSQL native `UUID` 전환은 별도 승인 migration 범위이며, 이 PR에서 타입을 섞지 않습니다.

구현 테이블:

| 영역 | 테이블 | 설명 |
| --- | --- | --- |
| Source | `rag_source`, `rag_source_endpoint`, `rag_source_operation` | 공식 Source와 endpoint·operation metadata. Runtime 사용은 기본 비활성 |
| Snapshot | `rag_source_snapshot`, `rag_source_snapshot_verification`, `rag_source_ingestion_run`, `rag_source_ingestion_artifact` | 수집 version, checksum, parser/normalization/canonicalization version, 검증 이력, 수집 실행 이력과 원본 저장소 참조 |
| Catalog | `rag_medication_product`, `rag_medication_ingredient`, `rag_medication_alias`, `rag_medication_product_component` | snapshot 단위 제품·성분·별칭·구성성분 참조 데이터 |

주요 제약:

- operation당 `CURRENT` snapshot은 최대 1개만 허용합니다. 여기서 `CURRENT`는 검증·최신성 상태이며, 실제 Runtime 사용 버전 선택은 Runtime Bundle에서 결정합니다. 새 Snapshot 검증 중에도 기존 승인 Bundle은 유지될 수 있습니다. 같은 operation에서 새 Snapshot을 `CURRENT`로 승격할 때는 기존 `CURRENT`를 먼저 `STALE`로 내린 뒤 새 Snapshot을 `CURRENT`로 전환합니다.
- `rag_source_snapshot`의 version, checksum, parser/normalization/canonicalization version, record count, 선행 snapshot 참조 등 불변 필드는 UPDATE할 수 없습니다.
- `rag_source_snapshot` 행은 DELETE할 수 없습니다. 재검증 결과는 `rag_source_snapshot_verification`에 append하고, 잘못된 snapshot은 새 snapshot 또는 forward-fix migration으로 정정합니다.
- Alias와 Component는 product/ingredient와 같은 `source_snapshot_id`를 가져야 하며, composite FK로 DB에서 강제합니다.
- `rejected_record_count`는 `record_count`보다 클 수 없습니다.
- `rag_source_ingestion_run.attempt_number`는 `run_group_key`가 가리키는 같은 수집 실행 안의 재시도 번호입니다. 같은 operation이어도 서로 다른 `run_group_key`의 독립 수집 실행은 attempt 1부터 다시 시작할 수 있습니다.
- `rag_source_ingestion_artifact`는 원본 바이트를 DB에 저장하지 않습니다. 접근 통제 저장소의 backend·object key, 페이지 번호, Artifact key, SHA-256, 크기와 content type만 수집 실행에 연결합니다.
- 로컬 저장 어댑터는 `sha256/{앞 2자리}/{SHA-256}.artifact` 형식의 내용 주소를 사용합니다. 완성 전 임시 파일은 참조하지 않으며 크기·checksum 검증과 파일 동기화가 끝난 뒤에만 mode `0600`의 불변 객체를 원자적으로 공개합니다. 저장소 디렉터리는 mode `0700`으로 제한합니다.
- S3 호환 비공개 저장 어댑터는 `{prefix}/sha256/{앞 2자리}/{SHA-256}.artifact`를 사용합니다. `If-None-Match: *`, SHA-256 upload checksum과 명시적으로 선택한 AES256 또는 KMS 서버 측 암호화를 요구하고, 기존 객체는 크기·content type·checksum metadata·암호화 상태가 모두 일치할 때만 재사용합니다. credential은 DB에 저장하지 않고 Worker 실행 역할 또는 표준 AWS credential provider chain으로 주입합니다.
- `NO_CHANGE`와 `SOURCE_VERSION_CONFLICT`를 포함한 모든 검증 실행은 자체 Artifact 참조를 보존합니다. Snapshot이 생성되지 않은 실행도 감사 가능한 원본 근거를 잃지 않습니다.
- 같은 수집 실행에서 페이지 번호와 Artifact key는 각각 중복될 수 없으며 Artifact 참조 행은 UPDATE·DELETE할 수 없습니다.
- `artifact_kind=RAW_RESPONSE`는 양의 페이지 번호를 가지며 거부 메타데이터를 가질 수 없습니다. `artifact_kind=REJECTS`는 페이지 번호 대신 안전한 고정 `reject_code`와 원문을 포함하지 않는 `parser_location`을 필수로 기록합니다.
- Artifact key·content type과 Snapshot 실행 metadata의 DB 길이 제한, REJECTS 위치의 제어문자, RAW_RESPONSE·REJECTS 전체의 중복 Artifact key는 파일 보존 전에 검사합니다.

Rollback 정책:

- Production에서는 Source/Catalog 테이블을 삭제하는 downgrade를 사용하지 않고 forward-fix migration을 사용합니다.
- 비운영 환경에서도 Source/Catalog 또는 Artifact 참조 테이블에 데이터가 하나라도 있으면 downgrade는 테이블 삭제 전에 중단됩니다.
- 빈 DB에서만 downgrade → upgrade 왕복을 허용합니다.

범위 제외:

- 실제 MFDS 네트워크 수집과 외부 Object Storage bucket·credential의 배포 환경 연결, Catalog 대량 적재 및 `ON CONFLICT` 기반 upsert
- DB rollback 뒤 참조되지 않은 내용 주소 객체의 보존·정리 정책과 REJECTS 보존 기간
- Source 승인·Runtime 활성화
- RAG 검색, Resolver ranking, Preflight 정책
- Candidate 결과와 Catalog product의 FK 연결 및 `CandidateCatalogSourceRef`

## Post-MVP schema-only 테이블

`knowledge_document`, `knowledge_chunk`, `guide_citation`, `chat_citation`, `ai_job_attempt`, `message_quarantine`, `dlq_outbox_event`는 migration과 SQLAlchemy 모델에는 존재하지만 현재 repository, service, API DTO와 응답에는 연결되지 않습니다. `ai_job`, `outbox_event`, `idempotency_record`는 `JobIntakeService`(#147)의 Job 접수 transaction에 연결되었고, `outbox_event`는 due row 선점·만료 claim 재선점·`WorkerMessage` 조립·Redis 발행·`claim_token` fencing 완료 경계(#219)에 연결되었습니다. 실제 OCR·Guide·Chat API DTO·응답 경로는 아직 연결되지 않았습니다(#148). 이 부분 연결을 RAG, 출처 인용, Citation·Safety 검증 또는 Track A 비동기 Job 실행 전체 완료로 해석하지 않습니다.

## Post-MVP-1 목표 스키마 — 분할 구현 중

Approved Contract Freeze v4와 Authority Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`의 RAG DB schema v1.47은 다음 구조를 목표로 승인했습니다. PostgreSQL 플랫폼 전환은 완료됐고, RAG/Eval 목표 스키마는 분할 PR 단위로 migration·모델·repository를 반영합니다. 이 섹션은 구현 상태를 함께 표시하며, 실제 도입 시 expand → backfill → 검증 → read cutover → contract 순서와 rollback 계획을 migration PR에서 확정합니다. 기존 Application ID/FK와 이번 분할 PR의 신규 RAG/Eval ID는 호환을 위해 `CHAR(36)`을 사용합니다. PostgreSQL native `UUID` 전환은 별도 승인 migration 범위입니다.

| 영역 | 목표 테이블 | 목표 제약 |
| --- | --- | --- |
| 처방 버전 | `prescription_version`, `prescription_version_medication` | 불변 snapshot과 처방별 단일 active version |
| OCR LLM provenance | OCR 구조화 실행·필드 provenance 계열 | `raw_value`, rule 정규화값, LLM 초안, 사용자 수정값, 확정값과 allowlist·schema·prompt·model·validator version 분리 |
| 복약 기록 | `medication_schedule`, `medication_occurrence`, `medication_checkin`, audit | Check-in 3결과, occurrence별 단일 현재 결과, 정정 이력 보존 |
| Barrier·Support | `safety_assessment`, `barrier_response`, `support_action_plan`, follow-up | Safety 우선, 거절과 미제출 구분, revision별 무효화 |
| 공식 Source·Catalog | `rag_source`, source approval·ingestion·normalization·snapshot·verification 계열, medication product·ingredient·component·alias | #164 최소 DB 기반은 구현 중. 실제 수집·적재·Runtime 활성화·검색 연결은 후속 |
| Candidate·Identification | candidate index·search·result, append-only medication identification | confirmed `medication_name + nullable strength_text`만 입력, 내부 Top-K와 외부 최대 1개 분리, 사용자 확인·거절·소유권·멱등성·현재성 |
| Rule·Evidence | `rag_interaction_rule`, `rag_rule_evidence`, rule set 계열 | 처방약–OTC Rule-first, 승인 evidence와 version 연결, rule 없음은 안전 판정이 아님 |
| RAG 실행·안전 결과 | retrieval run·signal·hit, result·claim·citation·safety 계열 | Job·처방 version·Runtime Bundle 귀속, 생성·검증·공개 상태축과 Citation 완전성 분리 |
| Runtime 배포 | runtime execution manifest·release bundle·environment 계열 | Source·Index·Rule·Prompt·Model·Validator·Worker artifact version을 환경별 단일 active bundle로 고정 |
| Evaluation | `eval_dataset`, `eval_case`, `eval_experiment`, `eval_variant`, `eval_run`, `eval_case_result`, `eval_metric`, `eval_failure` 최소 DB 기반 구현 중(#164) | `HOLDOUT`·`SAFETY_REGRESSION`·`END_TO_END_RAG`, 분모·신뢰구간과 재현 version 저장. `eval_run`은 `dataset_id + dataset_manifest_hash`가 실제 Dataset manifest와 일치해야 하고, `eval_case_result`는 Run·Case의 `dataset_id + experiment_type` 혼용을 DB에서 차단합니다. 미실행은 `execution_status=NOT_EVALUATED`, `decision_status=null`; 실행 완료(`COMPLETED`)는 `decision_status`를 반드시 기록하며 분모·표본·독립 Group 부족일 때만 `INCONCLUSIVE`입니다. Runner·Release approval·Runtime 활성화 연결은 후속 |

Evaluation의 `question_template`, `source_segment`, `non_sensitive_summary`, `non_sensitive_context`는 합성 template/segment 식별자, metric 이름·개수, enum code, artifact reference 같은 비민감 구조화 값만 허용합니다. 자유 텍스트, 모델 출력, retrieved chunk, 실제 환자정보, OCR 원문, 처방 원문은 저장하지 않습니다.

OCR Candidate Index와 의료 Evidence Index는 별도 version과 물리 경계를 가지며, pgvector는 OCR 후보 보조 단계에만 사용합니다. HIRA 적용약가 데이터는 공식 제품 식별 입력·정답 원장·상호작용 근거로 사용하지 않습니다.

`OTC_IDENTIFICATION`, `OTC_EVALUATION`, `OTC_RULE_MATCH` 같은 Track D 전용 평가 모델은 목표 schema에서 사용하지 않습니다. OTC는 기존 Chat 결과·Citation을 재사용하지만 `interaction_rule`과 `rule_evidence`는 Track F 내부 결정 규칙과 근거 원장으로 유지합니다.

상세 목표는 [계약 인덱스](./contracts/README.md)의 v1 문서를 따릅니다. 각 행의 구현 상태에 명시되지 않은 목표 enum·컬럼은 현재 코드가 이미 사용한다고 설명하지 않습니다.

### Source Verification 후속 보호 (#165 / PR #323)

- `165d7e6f5041`은 `rag_source_snapshot_verification`의 UPDATE·DELETE를 DB trigger로 차단한다.
- `snapshot-publication-approval = PASSED`는 비어 있지 않은 `verified_by`가 필요하다. 기존 익명 승인은 자동 변환하지 않으며 migration 적용 전에 검토해야 한다.
- Verification 이력이 있으면 해당 보호를 제거하는 downgrade를 차단한다.
- FAILED Snapshot도 같은 Source version의 충돌 비교에 포함하지만 `NO_CHANGE` 재사용은 금지한다. FAILED는 계보에서 제외하며, 이전 비FAILED Snapshot이 없으면 NULL이다. FAILED 저장 모델의 정본 정렬은 #164 후속 범위다.
- REJECTS Artifact는 거부 record와 1:1이며 파일·DB 저장 전 개수 일치를 검사한다.
