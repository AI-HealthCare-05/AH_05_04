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
- 필수 필드(`PRESCRIBED_DATE`, `DOSE_VALUE`, `FREQUENCY_PER_DAY`, `DURATION_DAYS`)는 OCR이 인식하지 못해도 위 빈 검수 필드(`raw_value=null`, `confirmation_status=UNCONFIRMED`) row가 항상 보장됩니다(#294) — `PRESCRIBED_DATE`(index 0)는 항상, 나머지는 이미 감지된 `medication_index`에 한해서만 채우며, 약물 자체가 하나도 감지되지 않은 경우는 포함하지 않습니다. `MEDICATION_STRENGTH`·`DOSE_UNIT`·`TIMING`은 선택 필드라 이 보장 대상이 아니며, `MEDICATION_NAME`은 계약상 빈 필드로 만들지 않으므로 이 목록에서 제외됩니다. 정본은 OCR 구조화 계층(`docs/contracts/current/ocr-medication-structuring.md`의 「부분 인식」)이고, OCR 결과를 저장하는 `ai_worker/adapters/sqlalchemy_ocr_result_store.py`는 회귀 방지를 위한 방어 계층으로만 동일 필드를 다시 채웁니다.
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

Revision `164f3a2b1c0d`는 #164의 후속 적재 준비를 위해 Source/Snapshot/Catalog 최소 DB 기반을 추가합니다. Revision `165a4b3c2d1e`는 수집 실행별 원본 Artifact 참조와 무결성 메타데이터를 추가하고, `165b5c4d3e2f`는 거부 원문의 안전한 추적 필드를 추가합니다. 이번 문서 정렬은 새 정본 계약을 만들지 않고, 기존 `docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md`와 `docs/contracts/targets/post-mvp-1/medication-identification-v1.md` 기준을 data schema·traceability 문서에 흡수합니다.

이번 분할 범위의 ID/FK 매핑은 기존 애플리케이션 호환성을 우선해 `UUIDChar` 기반 `CHAR(36)`을 사용합니다. 신규 독립 RAG/Eval ID의 PostgreSQL native `UUID` 전환은 별도 승인 migration 범위이며, 이 PR에서 타입을 섞지 않습니다.

구현 테이블:

| 영역 | 테이블 | 설명 |
| --- | --- | --- |
| Source | `rag_source`, `rag_source_endpoint`, `rag_source_operation` | 공식 Source와 endpoint·operation metadata. Runtime 사용은 기본 비활성 |
| Snapshot | `rag_source_snapshot`, `rag_source_snapshot_verification`, `rag_source_ingestion_run`, `rag_source_ingestion_artifact` | 수집 version, checksum, parser/normalization/canonicalization version, 검증 이력, 수집 실행 이력과 원본 저장소 참조 |
| Catalog | `rag_medication_product`, `rag_medication_ingredient`, `rag_medication_alias`, `rag_medication_product_component` | snapshot 단위 제품·성분·별칭·구성성분 참조 데이터 |

Source/Snapshot 책임 경계:

| 테이블 | 책임 | Runtime 활성화와의 관계 |
| --- | --- | --- |
| `rag_source` | 공식 Source의 정적 식별자, 표시명, 라이선스·출처 표기, lifecycle 상태를 보관 | Source 등록 자체는 Runtime 사용을 의미하지 않음 |
| `rag_source_endpoint` | Source 하위 endpoint와 수집 승인 상태, endpoint 단위 runtime 사용 가능 상태를 보관 | `runtime_status`는 endpoint 사용 가능성만 나타내며 특정 Snapshot 선택은 하지 않음 |
| `rag_source_operation` | endpoint 하위 operation의 stable provenance 단위와 수집 승인 상태를 보관 | Operation은 Snapshot 생성 범위이며 Runtime Bundle의 사용 버전 선택과 분리 |
| `rag_source_snapshot` | 특정 operation 수집·정규화 결과의 불변 Snapshot과 checksum·version·record count를 보관 | `CURRENT`는 검증·최신성 상태이고 Runtime 활성 Snapshot 포인터가 아님 |
| `rag_source_ingestion_run` | 수집/정규화 실행 시도, 재시도 scope, 성공/실패/NO_CHANGE 결과를 보관 | 실행 이력이며 성공이 곧 Runtime 사용 승인을 뜻하지 않음 |
| `rag_source_snapshot_verification` | Snapshot 검증 결과를 이력으로 보관하는 최소 구조를 제공 | DB 차원의 UPDATE/DELETE 방지와 publication 상태 전이는 #323에서 강제 |

Downstream provenance 연결 기준:

| 소비 영역 | 기준 provenance key | 현재/후속 책임 |
| --- | --- | --- |
| Source 원본 | `rag_source.source_code`, `owner_name`, `license_name`, `attribution_text` | Source 자체의 정적 출처·라이선스·표기 책임. 원문 payload 저장은 #165 Raw Artifact 상세 구조에서 분리 |
| Endpoint | `rag_source_endpoint.source_id + endpoint_code` | Source 하위 API endpoint 식별. endpoint 승인·비활성 상태는 신규 수집 차단 입력이며 Snapshot 선택 기준은 아님 |
| Operation | `rag_source_operation.endpoint_id + operation_code` | stable provenance operation 단위. Snapshot version unique 기준이며, 수집 동시 실행 lock 기준과는 분리 |
| Snapshot | `rag_source_snapshot.id`, 보조 표시값 `source_version`, checksum/version 필드 | 실제 Snapshot 특정은 ID 참조가 기준. `source_version` 단독 조회는 금지하고 operation과 함께만 사용 |
| Ingestion Run | `operation_id + run_group_key + attempt_number`, nullable `snapshot_id` | 수집/정규화 실행 이력과 재시도 scope. 성공·NO_CHANGE·실패 기록이며 Runtime 활성화와 분리 |
| Verification | `rag_source_snapshot_verification.snapshot_id`, `check_name`, `verification_result`, `verified_at` | Snapshot 검증 이력 저장 구조. append-only DB 강제와 DB-owned publication 상태 전이는 #323 범위 |
| Catalog Product / Ingredient / Alias / Component | 각 행의 `source_snapshot_id`; Alias/Component는 대상 row와 같은 `source_snapshot_id` composite FK | Catalog 행은 Snapshot 단위 publication row다. 안정 Identity/Set/manifest 확장은 #166에서 별도 정렬 |
| Candidate Index / Resolver 입력 | 안정 제품 tuple `code_system + canonical_code`, Candidate Index version/ref, Catalog manifest hash | 현재 Candidate는 tuple snapshot을 저장하고 `product_id` FK는 후속 연결. DB UUID를 공식 Identity로 사용하지 않음 |
| Evaluation evidence | `source_snapshot_ref`, `candidate_index_ref`, dataset/manifest hash | Evaluation은 문자열 ref와 manifest hash로 재현성 근거를 보관한다. 실제 Evidence/Citation FK 전체 구조는 후속 PR 범위 |

주요 제약:

- #164 최소 DB 기반의 Snapshot은 Source 전체가 아니라 Operation 단위 산출물로 둡니다. 따라서 version unique 축은 `(operation_id, source_version)`이며, 같은 Source의 서로 다른 Operation에 같은 `source_version`이 공존할 수 있습니다.
- #165의 동시 Acquisition lock은 `source_id` 단위입니다(#323 범위). 같은 Source의 서로 다른 Operation도 동시에 수집하지 않으며, Snapshot version unique 축과 수집 lock 축을 섞지 않습니다.
- 안정적인 수집 Operation은 `source_code + endpoint_code + operation_code`로 식별합니다. Evidence provenance는 `source_version` 단독이 아니라 `source_snapshot_id` 같은 snapshot 참조로 Snapshot을 특정합니다. `source_version`은 사람이 확인할 수 있는 version 값입니다.
- `rag_source_ingestion_run`은 정규 목표의 ingestion run과 normalization run을 합친 최소 실행 이력입니다. `NO_CHANGE` 재검증이 동일 Snapshot을 반복 참조할 수 있으므로 `snapshot_id` 전체 unique는 두지 않습니다.
- 이 최소 모델은 `rag-db-schema` v1.47의 Source 단위 Snapshot과 분리된 ingestion/normalization run 모델을 대체하지 않습니다. 정규 목표로 수렴할 때는 별도 Decision/Contract Freeze와 migration·테스트를 함께 갱신합니다.
- operation당 `CURRENT` snapshot은 최대 1개만 허용합니다. 여기서 `CURRENT`는 검증·최신성 상태이며, 실제 Runtime 사용 버전 선택은 Runtime Bundle에서 결정합니다. 새 Snapshot 검증 중에도 기존 승인 Bundle은 유지될 수 있습니다. 같은 operation에서 새 Snapshot을 `CURRENT`로 승격할 때는 기존 `CURRENT`를 먼저 `STALE`로 내린 뒤 새 Snapshot을 `CURRENT`로 전환합니다.
- 새 Snapshot을 `CURRENT`로 승격하는 작업은 같은 transaction 안에서 기존 `CURRENT` → `STALE` 전환과 신규 Snapshot `CURRENT` 전환을 함께 수행해야 합니다. 중간에 operation당 `CURRENT`가 2개가 되는 상태는 `uq_rag_source_snapshot_current`가 거부합니다. 현재 최소 DB 기반에는 승격 service가 없으므로 이 순서는 후속 Source ingestion/Catalog loader 구현에서 적용합니다.

Snapshot verification 상태 의미:

| 상태 | 의미 | Runtime 활성화와의 관계 |
| --- | --- | --- |
| `PENDING` | Snapshot 생성 또는 검증 대기 상태 | Runtime 사용 불가. Bundle 선택 대상이 아님 |
| `CURRENT` | 해당 operation에서 최신 검증 기준을 통과한 Snapshot | Runtime 활성 포인터가 아니며 Bundle이 별도로 선택해야 사용 가능 |
| `STALE` | 더 최신 Snapshot으로 대체되었거나 신규 사용 적격성을 잃은 과거 Snapshot | 신규 선택 대상은 아니지만 과거 provenance 재현을 위해 보존 |
| `FAILED` | 이미 생성된 candidate Snapshot이 후속 verification에서 실패해 사용 불가한 상태 | Runtime 사용 불가. 실패 이력은 verification에 보존하며 #323 정렬 범위에서 DB 보호를 강화 |

`SOURCE_VERSION_CONFLICT`는 failed ingestion run으로 기록하고 신규 Snapshot을 생성하지 않는 fail-closed 경로입니다. Snapshot `FAILED`와 Ingestion Run `FAILED`는 같은 의미가 아니며, #165 Source ingestion 계약의 실패 경계를 우선합니다.

`QUARANTINED`는 현재 `RagSnapshotVerificationStatus` 값이 아닙니다. 격리 상태가 필요하면 Source ingestion 정책과 공개 게이트를 먼저 확정한 뒤 별도 Decision/Contract Freeze와 migration·테스트로 추가합니다.

- `rag_source_snapshot`의 version, checksum, parser/normalization/canonicalization version, record count, 선행 snapshot 참조 등 불변 필드는 UPDATE할 수 없습니다.
- `rag_source_snapshot` 행은 DELETE할 수 없습니다. 재검증 결과는 `rag_source_snapshot_verification`에 새 이력으로 기록하고, 잘못된 snapshot은 새 snapshot 또는 forward-fix migration으로 정정합니다. Verification row의 DB 차원 UPDATE/DELETE 방지는 #323에서 구현합니다.
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

Ingestion Run / Receipt 기준:

| 항목 | 기준 | 의미 |
| --- | --- | --- |
| `run_group_key` | 같은 operation 안에서 하나의 수집 실행을 묶는 재시도 scope | 같은 수집 실행의 attempt는 같은 `run_group_key`를 공유하고, 독립 수집 실행은 새 `run_group_key`를 사용 |
| attempt 중복 방지 | `(operation_id, run_group_key, attempt_number)` unique | 같은 수집 실행에서 동일 attempt가 두 번 기록되는 것을 DB가 거부 |
| 독립 실행 | 같은 `operation_id`라도 서로 다른 `run_group_key`면 `attempt_number=1` 허용 | 예약/수동 재수집/재검증 같은 독립 실행을 같은 attempt 번호로 시작할 수 있음 |
| 성공 이력 | `run_status=SUCCEEDED`, nullable `snapshot_id`, finished metadata | 수집·정규화가 Snapshot으로 귀결된 실행 기록. Runtime 활성화는 아님 |
| 부분 성공 이력 | `run_status=SUCCEEDED_WITH_REJECTIONS` | 거부 record가 있었던 실행 기록. 자동 Runtime 편입으로 해석하지 않음 |
| 실패 이력 | `run_status=FAILED`, `failure_code`, `failure_message` | Snapshot 생성 전 acquisition/normalization/schema/version-conflict 실패를 추적. 신규 Snapshot은 생성하지 않고 실패 원문 payload도 저장하지 않음 |
| 검증 이력 | `rag_source_snapshot_verification`의 `verification_result` | Snapshot 검증 결과 저장 구조를 수집 실행 record와 구분. append-only DB 강제는 #323 범위 |
| raw manifest checksum | `raw_manifest_checksum` | Raw Artifact 메타데이터 집합의 결정적 checksum. 원본 바이트/파일 목록 무결성 기준 |
| canonical checksum | `canonical_checksum` | 성공적으로 해석된 전체 record의 canonical 내용 checksum. envelope 제외·정렬·정규화 규칙은 Operation 계약과 `canonicalization_spec_version`이 고정 |

Canonicalization / Checksum 기준:

| 항목 | 기준 | 적용 범위 |
| --- | --- | --- |
| 원본 필드값 보존 | 원문 문자열의 Unicode 형태와 앞뒤 공백을 그대로 보존 | Parser가 Snapshot checksum 입력과 raw/source-derived 저장값을 만들 때 NFC·trim을 적용하지 않음 |
| 숫자형 문자열 | 숫자형 문자열을 숫자 타입으로 변환하지 않음 | `"001"`과 `1`은 서로 다른 값으로 취급 |
| null/빈 문자열/누락 | `null`, 빈 문자열, 필드 누락을 서로 다른 canonical 값으로 취급 | 감사 재현성과 schema drift 판정 기준 |
| 객체 key 정렬 | 모든 중첩 객체 key는 UTF-16 big-endian byte lexicographic comparator로 정렬 | 구현 언어의 기본 문자열 정렬에 의존하지 않음 |
| 배열 순서 | 배열 내부 순서는 원본 순서를 유지 | 객체 key 정렬과 달리 배열 원소를 재정렬하지 않음 |
| 전체 record 정렬 | Operation Primary Key로 정렬. MFDS 제품 허가정보는 `ITEM_SEQ` 원문 문자열 기준 | `ITEM_SEQ` 누락·타입 불일치·중복은 거부 |
| canonicalization version | `rag_source_snapshot.canonicalization_spec_version`에 저장 | 규칙 변경 시 기존 Snapshot을 덮어쓰지 않고 새 version으로 새 Snapshot 생성 |
| checksum NFC | 제품 Source ingestion canonical checksum에는 NFC를 적용하지 않음 | 최신 `rag-source-ingestion-v1.md`의 `mfds-product-approval@1` 규칙을 따름. Synthetic guard manifest의 NFC 해싱과 혼용하지 않음 |
| trim | 원본·checksum 입력에는 trim을 적용하지 않음 | `normalized_product_name`, `normalized_ingredient_name`, `normalized_alias_text` 같은 matching 전용 필드에만 적용 가능 |

#164 최소 DB 기반은 위 규칙의 저장 위치와 provenance 경계를 고정합니다. 실제 Parser, canonical JSON 생성, Source 수집 및 Catalog 적재 배치는 #165/#166에서 이 규칙을 따라 구현합니다.

Catalog 적재 연결성:

| 항목 | 현재 제공 기준 | 후속 책임 |
| --- | --- | --- |
| Product record 조회 | `get_product_by_record_key(source_snapshot_id, source_record_key)` | #166 적재가 원본 record key로 기존 제품 row를 찾을 때 사용 |
| Ingredient record 조회 | `get_ingredient_by_record_key(source_snapshot_id, source_record_key)` | #166 적재가 원본 record key로 기존 성분 row를 찾을 때 사용 |
| Ingredient code 조회 | `get_ingredient_by_code(source_snapshot_id, ingredient_code_system, ingredient_code)` | #166 적재가 성분 코드 기반 중복·연결을 확인할 때 사용 |
| 적재 idempotency | unique/FK/CHECK 제약과 최소 조회 interface까지만 제공 | 대량 적재 재실행의 `ON CONFLICT DO NOTHING/UPDATE`, batch upsert, 충돌 복구 정책은 #166 범위 |
| 대량 적재 성능 | row 단위 create/get 골격만 제공 | N+1 회피, bulk insert/upsert, chunk size, partial failure 처리는 #166에서 확정 |

Rollback 정책:

- Production에서는 Source/Catalog 테이블을 삭제하는 downgrade를 사용하지 않고 forward-fix migration을 사용합니다.
- 비운영 환경에서도 Source/Catalog 또는 Artifact 참조 테이블에 데이터가 하나라도 있으면 downgrade는 테이블 삭제 전에 중단됩니다.
- 빈 DB에서만 downgrade → upgrade 왕복을 허용합니다.

범위 제외:

- 실제 MFDS 네트워크 수집, Parser/Normalizer 구현, Catalog 대량 적재 및 `ON CONFLICT` 기반 upsert
- 외부 Object Storage bucket·credential의 배포 환경 연결
- DB rollback 뒤 참조되지 않은 내용 주소 객체의 보존·정리 정책과 REJECTS 보존 기간
- Source 승인·Runtime 활성화·Production 공개 승인
- RAG 검색, Resolver ranking, Preflight 정책
- Candidate 결과와 Catalog product의 FK 연결 및 `CandidateCatalogSourceRef`

## RAG Evidence·Citation 최소 DB 기반

Revision `164c5d6e7f8a`는 #164의 Evidence/Citation 후속 구현을 위해 최소 DB 기반을 추가합니다. 이 변경은 새 정본 계약이 아니라 기존 `docs/contracts/targets/post-mvp-1/rag-runtime-v1.md`와 `docs/contracts/targets/post-mvp-1/safety-result-v2.md`의 공개·근거·Citation 경계를 data schema에 흡수하는 구현 PR 범위입니다.

구현 테이블:

| 영역 | 테이블 | 설명 |
| --- | --- | --- |
| Knowledge | `rag_evidence_knowledge` | Source Snapshot에서 유래한 비환자 근거 단위의 식별자, 제목, locator, digest를 보관 |
| Evidence | `rag_evidence` | Knowledge/Product/Ingredient와 같은 Snapshot 안에서 연결되는 승인 근거 단위 |
| Rule | `rag_evidence_rule` | Evidence에 연결되는 rule-first 판단 근거의 최소 식별자와 digest |
| Guideline | `rag_evidence_guideline` | Guide·limited response·safety fallback에 연결할 guideline 근거의 최소 식별자와 digest |
| Citation | `rag_citation` | Guide/Chat/RAG/Safety target의 claim과 Evidence를 연결하는 공개 가능성 검증 결과 |

주요 제약:

- Evidence는 `source_snapshot_id`를 기준 provenance로 사용합니다. Product·Ingredient·Knowledge를 참조할 때도 같은 Snapshot 행만 연결할 수 있도록 composite FK로 강제합니다.
- `PRODUCT_FACT`는 `product_id`, `INGREDIENT_FACT`는 `ingredient_id`가 반드시 있어야 하며, 제품·성분 근거가 참조 대상 없이 저장되지 않도록 CHECK 제약으로 막습니다.
- 이번 최소 DB 기반에서 `rag_citation.release_status`는 `NOT_PUBLIC`만 허용합니다. `PUBLIC` 공개와 `authorization_status=PASS`는 실제 Citation Authorization Guard Decision/Usage FK가 연결되는 후속 migration에서 엽니다.
- Evidence 승인 전이, revision 기반 교체, Citation Authorization Guard 연결, 공개 Release Gate는 이번 최소 DB 기반 범위가 아니며 #178 Evidence Gate, #180 Citation Authorization Guard, #181 Runtime Release/Activation 후속 구현에서 확정합니다.
- 의료 claim(`claim_kind=MEDICAL`)은 `PARTIALLY_SUPPORTED`로 공개할 수 없습니다. `CONTRADICTED`, `NOT_SUPPORTED` 상태도 공개 Citation이 될 수 없습니다.
- Citation 없이 일반 의료 답변이 공개되었다고 해석하지 않습니다. 의료 claim이 있는 Guide/Chat/RAG 답변 공개는 Claim-Citation 검증과 release gate 통과가 필요합니다. 단, 의료 claim이나 source-based citation이 없는 승인된 고정 fallback은 빈 Citation Guard 없이 공개될 수 있습니다.
- Evidence/Citation 계열 row는 append-only입니다. UPDATE·DELETE는 차단하고, 정정은 forward-fix migration으로 처리합니다. revision 기반 교체 경로는 #178/#180/#181 후속 전환 설계에서 추가합니다.
- downgrade는 빈 DB에서만 허용합니다. Evidence/Citation row가 있으면 downgrade를 중단하고 Production에서는 forward-fix를 사용합니다.

민감정보 저장 경계:

- `rag_evidence_*`, `rag_citation`에는 실제 환자정보, OCR 원문 전체, 처방 원문 전체, Provider raw response를 저장하지 않습니다.
- 저장 가능한 값은 Source Snapshot/Catalog FK, target 식별자, claim key, locator, digest, enum 상태 같은 비민감 provenance metadata로 제한합니다.
- `public_excerpt`는 Guard 연결 전까지 `NULL`만 허용합니다. 공개 excerpt 저장과 검증은 #180 Citation Authorization Guard 연결 후 열며, 환자 유래 텍스트·OCR 텍스트·처방 텍스트·Provider 원문 출력 저장 위치로 사용하지 않습니다.

범위 제외:

- 실제 Retrieval 구현
- Ranking / Resolver 구현
- LLM Provider 호출
- Guide/Chat 답변 생성 로직
- Citation 화면 표시
- Safety/Fallback 문구 생성
- Evidence 품질 평가 로직
- Runtime Bundle 활성화
- Production 공개 승인

## Prescription Version 이관과 Cleanup

Revision `169a1b2c3d4e`는 Expand 단계로 `prescription_version`, `prescription_version_medication`과 임시 nullable `prescription.active_version_id`를 추가했습니다. Revision `169b2c3d4e5f`는 기존 처방과 약물을 Version 1 snapshot으로 backfill했고, `169c3d4e5f6a`는 Prescription·Candidate·Identification·Guide·Chat read를 Version 기준으로 전환했습니다. Revision `169d4e5f6a7b`는 잘못된 provenance가 0건인지 잠금 검증한 뒤 `prescription.active_version_id`, `guide.prescription_version_id`, `chat_session.prescription_version_id`를 `NOT NULL`로 고정하고 `ai_job`의 유형별 Version 조건을 CHECK로 고정합니다.

| 관계 | 제약 |
| --- | --- |
| Version sequence | `(prescription_id, version_number)` unique, `version_number > 0` |
| 활성 Version | `NOT NULL`인 `(prescription.active_version_id, prescription.id)`가 `(prescription_version.id, prescription_version.prescription_id)`를 `DEFERRABLE INITIALLY DEFERRED`로 참조하므로 다른 처방의 Version을 가리킬 수 없고 Prescription → Version → Medication 원자 생성이 가능 |
| Version Medication | `(prescription_version_id, display_order)` unique, 양수 display order·dose·frequency·duration 및 비어 있지 않은 약명 CHECK. 지연 제약은 commit 시 모든 Version과 active pointer에 약물 1개 이상을 요구 |
| Snapshot 집합 동결 | Version INSERT trigger가 caller 입력을 덮어쓰고 DB의 epoch-aware top-level transaction ID를 internal `assembly_xid`에 기록. 현재 transaction ID가 같은 동안만 Medication INSERT를 허용하므로 release된 SAVEPOINT 뒤에도 조립 가능하고 custom GUC 위조 및 commit된 draft·active·historical Version 사후 INSERT 차단 |
| 불변성과 삭제 | Version/Medication 직접 UPDATE·DELETE 차단. 사용자 삭제는 `prescription`에서 시작하는 `ON DELETE CASCADE`만 허용하며, 지연 검증은 commit 전에 이미 연쇄 삭제된 행의 큐 이벤트를 건너뜀 |

이관 순서는 `Expand → Dual-write → Backfill → Verify → Read cutover → Cleanup`입니다. Cleanup 이후 신규 확정 writer와 모든 현재 read는 `prescription_version`·`prescription_version_medication`만 사용하고 legacy `medication`을 더 이상 dual-write하거나 조회하지 않습니다. legacy 테이블과 과거 row는 이관 감사·구 migration backfill 원본으로 보존하며 이 PR에서 삭제하지 않습니다. `prescription_id`가 Guide·Chat에 남아 있는 것은 소유권 및 composite FK의 부모 연결용이며 결과 snapshot의 현재성 기준은 반드시 `prescription_version_id`입니다.

`prescription_version_id`는 Prescription의 활성 포인터, Guide, Chat Session과 Candidate/Identification 파생 경로에서 필수입니다. `prescription_version_medication_id`는 Candidate Search·Identification에서 필수입니다. `ai_job.prescription_version_id`는 OCR Job에는 적용할 Prescription이 아직 없으므로 반드시 `NULL`이고, 확정 처방에서 파생되는 Guide·Chat Job에는 반드시 값이 있어야 합니다. `chk_ai_job_prescription_version_by_type`과 생성 서비스·저장소 검증이 이 조건을 함께 강제하며, 공통 Job DTO만 두 유형을 표현하기 위해 nullable 표면을 유지합니다.

Cleanup 뒤 schema downgrade는 Version 링크 컬럼을 다시 nullable로 바꿀 수 있을 뿐 application rollback을 복구하지 못합니다. Cutover 이후 생성된 처방에는 legacy `medication` row가 없으므로 구버전 애플리케이션을 배포하면 약물 목록이 비어 보입니다. 따라서 구버전 writer·reader로 되돌리는 배포는 금지하고, 같은 Version schema에서 현재 애플리케이션 재배포 또는 forward-fix만 허용합니다.

Production에서는 생성된 처방 version을 제거하는 migration downgrade 대신 forward-fix를 사용합니다. PR 2 revision의 downgrade는 snapshot을 보존하는 no-op이며 재-upgrade 시 완성된 graph를 검증·재사용합니다. PR 1의 schema downgrade는 Version 또는 Version Medication row가 있으면 계속 중단됩니다. 이는 계정·환자 데이터 삭제 시 부모 Prescription에서 시작하는 runtime cascade와 구분합니다.

## Post-MVP schema-only 테이블

`knowledge_document`, `knowledge_chunk`, `guide_citation`, `chat_citation`, `ai_job_attempt`, `message_quarantine`, `dlq_outbox_event`는 migration과 SQLAlchemy 모델에는 존재하지만 현재 repository, service, API DTO와 응답에는 연결되지 않습니다. `ai_job`, `outbox_event`, `idempotency_record`는 `JobIntakeService`(#147)의 Job 접수 transaction에 연결되었고, `outbox_event`는 due row 선점·만료 claim 재선점·`WorkerMessage` 조립·Redis 발행·`claim_token` fencing 완료 경계(#219)에 연결되었습니다. 실제 OCR·Guide·Chat API DTO·응답 경로는 아직 연결되지 않았습니다(#148). 이 부분 연결을 RAG, 출처 인용, Citation·Safety 검증 또는 Track A 비동기 Job 실행 전체 완료로 해석하지 않습니다.

## Post-MVP-1 목표 스키마 — 분할 구현 중

### Track B Schedule·Occurrence DB 기반 (#199)

Revision `199a1b2c3d4e`는 `medication_schedule`, `medication_schedule_time`,
`medication_occurrence` 테이블을 추가한다. Schedule은 #169의 불변
`prescription_version_medication.id`를 FK로 참조하며 약품 snapshot당 하나만 존재한다.
Schedule time은 `(medication_schedule_id, schedule_revision, local_time)`, occurrence는
`(medication_schedule_time_id, scheduled_local_date)`를 unique로 고정한다. 상태 enum 값,
revision 양수, 종료일 조합, occurrence deadline 순서는 DB CHECK로도 제한한다.

Repository의 기본 소유권 adapter는
`prescription_version_medication → prescription_version → prescription.profile_id → SELF user_id`
parent chain을 사용하며 기존 `medication.id`로 fallback하지 않는다. 새 schedule 생성은 현재 active
Version의 본인 약품만 허용한다. 과거 Version에 이미 생성된 schedule·occurrence의 이력 조회는
같은 SELF 소유자에게 유지하고, 다른 사용자는 존재 여부를 숨길 수 있도록 `None`을 반환한다.
aware datetime은 저장 전에 UTC instant로 정규화하며 PostgreSQL `timestamptz` 컬럼에 저장한다.

이 revision의 downgrade는 세 테이블을 잠근 뒤 Track B row가 한 건이라도 있으면 중단한다. 비어 있는
개발·검증 환경에서만 occurrence → schedule time → schedule 순서로 신규 테이블을 제거한다. 실제 데이터가
생성된 환경은 downgrade로 이력을 삭제하지 않고 동일 schema에서 forward-fix한다.

### Track B Rolling Occurrence·Version 변경 처리 (#200)

Scheduler는 `Asia/Seoul`로 확인된 서비스 시간대에서 실행하며 실행일을 포함한 14개 local date만
생성한다. 활성 Prescription Version에 속한 `ACTIVE` Schedule의 현재 revision time만 대상으로 삼고,
Schedule 시작일·종료일로 범위를 자른다. `(medication_schedule_time_id, scheduled_local_date)` unique와
PostgreSQL `ON CONFLICT DO NOTHING`을 함께 사용하므로 같은 horizon을 반복하거나 여러 실행이 경쟁해도
occurrence는 중복되지 않는다. `scheduled_at`과
`max(다음 KST 자정, scheduled_at + 4시간)`인 `confirmation_deadline_at`은 UTC instant로 snapshot한다.
종료일이 지난 활성 Schedule을 `ENDED`로 전환하는 주체도 Scheduler다.
운영 scheduler는 app image의 one-shot management command
`uv run --no-sync python -m app.commands.generate_medication_occurrences`를 KST 날짜가 바뀐 뒤 최소 하루 한 번
호출한다. command는 실행마다 독립 transaction을 열어 성공 시 commit하고 실패 시 rollback하므로 cron이나
동등한 배포 scheduler가 안전하게 재시도할 수 있다. 구체적인 실행 주기는 배포 scheduler가 관리하며,
여러 실행이 겹쳐도 위 unique·conditional insert가 중복을 막는다.

Schedule 생성은 SELF 소유권과 active Version을 확인할 때 부모 `PRESCRIPTION` row를 먼저 잠근다.
처방 정정도 같은 row부터 잠그므로 active 확인과 insert 사이에 Version이 교체되는 TOCTOU를 막는다.
처방 정정 transaction은 `PRESCRIPTION → AI_JOB → domain row → OUTBOX` 잠금 순서에서 이전 Version의
`effective_at` 이후 `PENDING` occurrence만 `CANCELLED`로 바꾼다. effective 시각 이전 occurrence와
`CLOSED|CANCELLED` occurrence, Schedule·ScheduleTime 이력은 그대로 보존하고 새 Version에 Schedule이나
occurrence를 복사하지 않는다. Outbox 취소 대상은 이 transaction에서 실제 `STALE`로 전환된
`PENDING|PROCESSING|RETRY_WAIT` Job ID로 제한하므로, 이미 `COMPLETED`인 Job의 미발행 이벤트는 변경하지
않는다. 취소된 occurrence ID 목록은 B5가 같은 transaction에서 미전달 알림만
취소할 수 있는 동기 연동 경계이며, Notification 저장 구현 자체는 B5 범위다. B5 구현 PR은
`get_prescription_version_medication_invalidation_service`에서 같은 session을 사용하는 Notification 취소
adapter를 반드시 주입하고 Version 정정 transaction의 동시 취소 테스트를 추가해야 한다. Check-in·API는
B3~B4 후속 범위다.

Approved Contract Freeze v4와 Authority Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`의 RAG DB schema v1.47은 다음 구조를 목표로 승인했습니다. PostgreSQL 플랫폼 전환은 완료됐고, RAG/Eval 목표 스키마는 분할 PR 단위로 migration·모델·repository를 반영합니다. 이 섹션은 구현 상태를 함께 표시하며, 실제 도입 시 expand → backfill → 검증 → read cutover → contract 순서와 rollback 계획을 migration PR에서 확정합니다. 기존 Application ID/FK와 이번 분할 PR의 신규 RAG/Eval ID는 호환을 위해 `CHAR(36)`을 사용합니다. PostgreSQL native `UUID` 전환은 별도 승인 migration 범위입니다.

| 영역 | 목표 테이블 | 목표 제약 |
| --- | --- | --- |
| 처방 버전 | Version 1 backfill·정정 Version·하위 FK·read cutover·cleanup 구현 | 활성 pointer와 Guide·Chat Version FK는 NOT NULL, 신규 writer와 현재 read는 Version snapshot 단일 기준 |
| OCR LLM provenance | OCR 구조화 실행·필드 provenance 계열 | `raw_value`, rule 정규화값, LLM 초안, 사용자 수정값, 확정값과 allowlist·schema·prompt·model·validator version 분리 |
| 복약 기록 | `medication_schedule`, `medication_occurrence`, `medication_checkin`, audit | Check-in 3결과, occurrence별 단일 현재 결과, 정정 이력 보존 |
| Barrier·Support | `safety_assessment`, `barrier_response`, `support_action_plan`, follow-up | Safety 우선, 거절과 미제출 구분, revision별 무효화 |
| 공식 Source·Catalog | `rag_source`, source approval·ingestion·normalization·snapshot·verification 계열, medication product·ingredient·component·alias | #164 최소 DB 기반은 반영 완료. source_version 상한·external_version은 #362 확정 후 별도 migration이며, Citation FK 때문에 `rag_source_snapshot.source_version`과 `rag_citation.source_version`을 같은 migration에서 함께 정렬합니다. 실제 수집·적재·Runtime 활성화·검색 연결은 후속 |
| Candidate·Identification | candidate index·search·result, append-only medication identification | confirmed `medication_name + nullable strength_text`만 입력, 내부 Top-K와 외부 최대 1개 분리, 사용자 확인·거절·소유권·멱등성·현재성 |
| Rule·Evidence | `rag_evidence_knowledge`, `rag_evidence`, `rag_evidence_rule`, `rag_evidence_guideline`, rule set 계열 | Evidence/Citation 최소 DB 기반은 PR #369에서 추가. 처방약–OTC Rule-first 실행, ranking, resolver, 품질 평가와 Runtime 활성화는 후속 |
| RAG 실행·안전 결과 | retrieval run·signal·hit, result·claim·citation·safety 계열, `rag_citation` | `rag_citation`은 Evidence와 동일 Source Snapshot 및 `source_version`에 묶인 claim-Evidence 비공개 저장 기반만 제공합니다. 이번 최소 DB 기반의 Evidence 상태는 `DRAFT`/`APPROVED`를 명시 입력으로만 사용하고, Citation 공개 상태는 `NOT_PUBLIC`만 사용합니다. STALE·retire·revoke lifecycle과 공개 Guard 연결은 #178/#180/#181 후속 전환 설계에서 추가합니다. 실제 Retrieval, Provider 호출, 답변 생성, Safety/Fallback 문구 생성, 화면 표시는 후속 |
| Runtime 배포 | `rag_runtime_execution_manifest`, `rag_runtime_release_bundle`, `rag_runtime_bundle_source`, `rag_runtime_environment`, `rag_runtime_environment_transition`, `rag_release_evaluation_approval` 최소 DB 기반 반영 완료 | Source Snapshot과 Evaluation Run은 FK로 결속하고 Candidate Index는 #168 전까지 ref/hash로만 보관합니다. Evaluation PASS는 release approval 입력일 뿐 자동 Runtime 활성화가 아니며, 실제 activation·rollback·Production 공개는 후속 범위입니다. |
| Evaluation | `eval_dataset`, `eval_case`, `eval_experiment`, `eval_variant`, `eval_run`, `eval_case_result`, `eval_metric`, `eval_failure` 최소 DB 기반 반영 완료 | `HOLDOUT`·`SAFETY_REGRESSION`·`END_TO_END_RAG`, 분모·신뢰구간과 재현 version 저장. `eval_run`은 `dataset_id + dataset_manifest_hash`가 실제 Dataset manifest와 일치해야 하고, `eval_case_result`는 Run·Case의 `dataset_id + experiment_type` 혼용을 DB에서 차단합니다. 미실행은 `execution_status=NOT_EVALUATED`, `decision_status=null`; 실행 완료(`COMPLETED`)는 `decision_status`를 반드시 기록하며 분모·표본·독립 Group 부족일 때만 `INCONCLUSIVE`입니다. Runner·Release approval·Runtime 활성화 연결은 후속 |

Evaluation의 `question_template`, `source_segment`, `non_sensitive_summary`, `non_sensitive_context`는 합성 template/segment 식별자, metric 이름·개수, enum code, artifact reference 같은 비민감 구조화 값만 허용합니다. 자유 텍스트, 모델 출력, retrieved chunk, 실제 환자정보, OCR 원문, 처방 원문은 저장하지 않습니다.

Runtime Bundle 최소 DB 기반은 `rag_runtime_execution_manifest`로 실행 manifest와 Worker/model/prompt/parser/resolver/guard reference를 고정하고, `rag_runtime_release_bundle`로 bundle manifest hash와 Candidate/Knowledge index ref/hash를 보관합니다. `rag_runtime_bundle_source`는 bundle이 선택한 `rag_source_snapshot.id`를 직접 FK로 기록합니다. `rag_release_evaluation_approval`은 `bundle_id + bundle_manifest_hash`와 `eval_run.id + decision_status`를 FK로 묶어 승인 기록이 정확한 Bundle manifest와 실제 PASS Run에서 벗어나지 않도록 하며, `rag_runtime_environment`는 active bundle id와 manifest hash를 함께 저장합니다. `rag_runtime_environment_transition`은 activation·rollback·resume·suspend 시 이전/이후 bundle pointer와 Guard Decision ref를 append-only 이력으로 보존합니다. 활성 여부의 기준 원본은 `rag_runtime_environment.active_bundle_id + active_bundle_manifest_hash` 포인터이며, `rag_runtime_release_bundle.bundle_status`에는 `ACTIVE` 값을 두지 않습니다. 이 구조는 저장 기반만 제공하며 Runtime 활성화 transaction, drain, mixed worker rollback, Candidate Index FK 연결, Production 공개 승인은 후속 PR에서 처리합니다.

Runtime 수정 가능 범위는 다음처럼 구분합니다. `rag_runtime_environment_transition`은 DB trigger로 UPDATE·DELETE를 차단하는 append-only 이력입니다. `rag_runtime_environment`는 환경별 현재 active bundle pointer, status, revision, safety epoch를 보관하는 mutable pointer 테이블이며 실제 변경 transaction과 drain 검증은 후속 Runtime activation PR 범위입니다. `rag_runtime_execution_manifest`, `rag_runtime_release_bundle`, `rag_runtime_bundle_source`, `rag_release_evaluation_approval`은 이번 최소 기반에서 생성·참조용 저장 구조와 FK/unique/CHECK만 제공하며, publish 이후 정정은 row 덮어쓰기보다 새 bundle/approval/forward-fix로 처리하는 정책을 후속 activation·approval 구현에서 고정합니다.

OCR Candidate Index와 의료 Evidence Index는 별도 version과 물리 경계를 가지며, pgvector는 OCR 후보 보조 단계에만 사용합니다. HIRA 적용약가 데이터는 공식 제품 식별 입력·정답 원장·상호작용 근거로 사용하지 않습니다.

`OTC_IDENTIFICATION`, `OTC_EVALUATION`, `OTC_RULE_MATCH` 같은 Track D 전용 평가 모델은 목표 schema에서 사용하지 않습니다. OTC는 기존 Chat 결과·Citation을 재사용하지만 `interaction_rule`과 `rule_evidence`는 Track F 내부 결정 규칙과 근거 원장으로 유지합니다.

상세 목표는 [계약 인덱스](./contracts/README.md)의 v1 문서를 따릅니다. 각 행의 구현 상태에 명시되지 않은 목표 enum·컬럼은 현재 코드가 이미 사용한다고 설명하지 않습니다.

### Source Verification 후속 보호 (#165 / PR #323)

- `165d7e6f5041`은 `rag_source_snapshot_verification`의 UPDATE·DELETE를 DB trigger로 차단한다.
- `snapshot-publication-approval = PASSED`는 비어 있지 않은 `verified_by`가 필요하다. 기존 익명 승인은 자동 변환하지 않으며 migration 적용 전에 검토해야 한다.
- Verification 이력이 있으면 해당 보호를 제거하는 downgrade를 차단한다.
- FAILED Snapshot도 같은 Source version의 충돌 비교에 포함하지만 `NO_CHANGE` 재사용은 금지한다. FAILED는 계보에서 제외하며, 이전 비FAILED Snapshot이 없으면 NULL이다. FAILED 저장 모델의 정본 정렬은 #164 후속 범위다.
- REJECTS Artifact는 거부 record와 1:1이며 파일·DB 저장 전 개수 일치를 검사한다.

### Source Snapshot 상태 전이 보호 (#165 / #323)

Revision `165e8f706152`는 일반 비소유자 Runtime 역할의 Snapshot 상태·검증/선택 timestamp 직접 UPDATE와 non-PENDING INSERT를 차단한다. `transition_rag_source_snapshot` DB 함수만 허용 전이와 rejected Snapshot의 named publication 승인을 검사한 뒤 CURRENT 상태와 immutable selection Verification을 함께 기록한다. migration owner와 Runtime 역할은 분리한다. 실제 Source Runtime·외부 승인 활성화는 여전히 후속 범위다. 상세 계약은 Source Target의 DB-owned 경계 절과 2026-09-08 Source Snapshot DB transition Decision을 따른다.
