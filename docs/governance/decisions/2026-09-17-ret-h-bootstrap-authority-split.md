# Decision: RET-H AWS Smoke Synthetic Index Bootstrap 권한 분리 및 런타임 격리

- **문서 ID**: `PD-684-20260917`
- **일자**: 2026-09-17
- **상태**: Approved
- **승인 근거**: https://github.com/AI-HealthCare-05/AH_05_04/issues/684#issuecomment-5706227386
- **구현 담당자**: 정현우 (`@ceohwj`, AI/RAG)
- **단일 책임 리뷰어**: 송은영 (`@phina-io`, Backend / Data & Security Technical Controls)
- **교차 영향 도메인**: 권가빈 (`@hazelnutflavoured`, PM / Track C 책임자), 김지혜 (`@Jye-rookie`, Worker / Source Provenance)
- **선행 조건 충족**: PR #683 merged (383daf73), PR #698 merged (66b91479, Issue #689 해소)
- **관련 Issue / PR**: Issue #684, PR #683, Issue #689 (PR #698), #178 / #273 / #634 후속

---

## 1. 배경 및 문제 제기

PR #683(RET-H AWS 실환경 synthetic smoke 검증)은 대상 PostgreSQL 환경에서 실제 프로덕션 검색 및 게이트 파이프라인(`PostgresqlEvidenceSearchAdapter`, `HybridRetrievalExecutor`, Reciprocal Rank Fusion, `PostgreSqlEvidenceEligibilityVerifier`, `SqlAlchemyRetrievalRunStore`)을 검증하기 위한 최소 synthetic evidence 색인과 실행 컨텍스트를 필요로 한다.

그러나 현재 저장소 구조에서 다음 세 가지 구조적 경계 문제가 존재한다:

1. **단일 Bootstrap 함수의 권한 모순**:
   - 기존 `ai_worker/tasks/evaluation/actual_retrieval_index.py`의 `bootstrap_dev_knowledge_index()`는 `rag_source*`(Source 계층)와 `knowledge_*`, `rag_knowledge_index*`(Knowledge 계층) 생성을 단일 실행 흐름 및 단일 DB 세션에서 수행한다.
   - 하지만 배포 및 DB 보안 모델(`infra/python/source_role_policy.py`, `infra/python/knowledge_index_role_policy.py`, `infra/python/provision_database_roles.py`)에서 `SOURCE_WRITER_USER`는 Source 테이블 쓰기 권한만 갖고 Knowledge 테이블 쓰기 시 `SQLSTATE 42501`(권한 거부)이 발생하며, `KNOWLEDGE_INDEX_BUILDER_USER`는 Knowledge/Index 테이블 쓰기 권한만 갖고 Source 테이블 INSERT 시 `SQLSTATE 42501`이 발생한다.
   - 두 권한을 동시에 가진 비특권(NOSUPERUSER) 애플리케이션 계정은 존재하지 않으며, 단일 프로세스에 두 쓰기 권한을 통합 부여하는 것은 최소 권한 원칙(Least Privilege)과 프로덕션 보안 불변식을 위반한다.

2. **#273 Synthetic Corpus 및 평가 인덱스 불변성 보존**:
   - 기존 #273 기반 synthetic dev 인덱스(`rag-natural-language-retrieval-dev-synthetic-index`, 100건 문장)는 평가 파이프라인의 기준 데이터(`evals/**`)로서 SHA-256 해시가 동결되어 있다.
   - AWS smoke 실행을 위해 기존 100건 코퍼스를 수정하거나 런타임에 sentinel 문자열을 동적으로 덧붙이는(append) 방식은 정규화 해시 불일치와 캐시 무효화를 유발한다.

3. **런타임 의존 엔티티(`user`, `ai_job`) 생명주기와 큐 오염 방지**:
   - `retrieval_run` 테이블은 외래 키 `fk_retrieval_run_job_id`를 통해 `ai_job.id`를 필수로 참조하며, `ai_job`은 `user.id`를 필수로 참조한다.
   - Smoke 검증을 위해 생성되는 `user`와 `ai_job`이 실제 Redis 작업 큐(`oryak:jobs`)로 인큐되거나 `outbox_event`를 생성하여 백그라운드 Worker에 의해 소비되는 사고를 원천 방지해야 하며, 테스트 종료 후 잔류 데이터가 `DB_APP_USER` 권한 내에서 외래 키 제약 위반 없이 안전하게 정리(cleanup)되어야 한다.

본 결정은 Issue #684의 구현 착수 전, 위 미결정 사항을 명시적인 Architecture Decision으로 정립하여 책임 리뷰어의 사전 승인을 확보하는 것을 목적으로 한다.

---

## 2. 핵심 결정 사항 (Candidate Decisions)

### A. 2단계 권한 분리 (2-Stage Authority Split)

기존 DB role policy(`source_role_policy.py`, `knowledge_index_role_policy.py`)의 권한(grant)은 일체 확대하지 않으며, Bootstrap 프로세스를 독립된 2단계 원샷 서비스로 완전 분리한다.

```text
[Stage 1: Source Hierarchy Generation]
Cred: SOURCE_WRITER_USER
Actions:
  - rag_source (SYNTHETIC_RET_H_SMOKE) 생성/확인
  - rag_source_endpoint 생성/확인
  - rag_source_operation 생성/확인
  - rag_source_snapshot 생성 (PENDING)
  - rag_source_snapshot_member 생성
  - Snapshot 검증 및 봉인 (rag_source_snapshot_verification 생성, verification_status='CURRENT')
  - Transaction COMMIT
Role Boundary:
  - SOURCE_TABLES (SELECT, INSERT, 제한적 UPDATE)만 허용
  - Knowledge 테이블 쓰기 시도 시 PostgreSQL SQLSTATE 42501로 fail-closed

[Stage 2: Knowledge Evidence Index Materialization]
Cred: KNOWLEDGE_INDEX_BUILDER_USER
Actions:
  - Stage 1 Source 계층 DB 재검증 (Preflight)
    * rag_source, rag_source_endpoint, rag_source_operation 상태 검증
    * rag_source_snapshot verification_status='CURRENT' 및 verification_seal_id 유효성 검증
    * rag_source_snapshot_member 갯수 및 locator, content_sha256 일치 검증
    * Preflight 실패 시: 0 embedding API 호출, 0 Knowledge 쓰기로 즉시 중단 (Fail-Closed)
  - 승인된 임베딩 호출 (text-embedding-3-large, 1536 dim, COSINE)
  - knowledge_document 생성
  - knowledge_chunk 생성
  - rag_knowledge_index 생성
  - rag_knowledge_index_member 생성
  - Transaction COMMIT
Role Boundary:
  - KNOWLEDGE_INDEX_WRITE_TABLES (SELECT, INSERT) 및 KNOWLEDGE_INDEX_SOURCE_READ_TABLES (SELECT)만 허용
  - Source 테이블 INSERT/UPDATE 시도 시 PostgreSQL SQLSTATE 42501로 fail-closed
  - KNOWLEDGE_INDEX_BUILDER는 rag_source_snapshot_verification에 대한 SELECT 권한이 없으므로(knowledge_index_role_policy.py 제약), Preflight 검증은 권한이 부여된 rag_source_snapshot의 verification_status, verification_seal_id, canonical_checksum을 통해 직접 수행하며 grant 확장을 요구하지 않는다.
```

- **컨테이너 자격 증명 격리**:
  - Stage 1 컨테이너는 `SOURCE_WRITER_USER`, `SOURCE_WRITER_PASSWORD`만 주입받는다.
  - Stage 2 컨테이너는 `KNOWLEDGE_INDEX_BUILDER_USER`, `KNOWLEDGE_INDEX_BUILDER_PASSWORD` 및 `OPENAI_API_KEY`만 주입받는다.
  - 두 컨테이너 모두 `DB_ADMIN_PASSWORD` 또는 `DB_MIGRATION_PASSWORD`를 일절 주입받지 않는다.
  - 상시 실행 컨테이너인 `fastapi`와 `ai-worker`에는 `KNOWLEDGE_INDEX_BUILDER` 또는 `SOURCE_WRITER` 자격 증명을 주입하지 않는다.
- **멱등성 보장**:
  - 이미 동일한 canonical hash를 가진 snapshot 및 index가 존재하는 경우, 읽기 전용 검증을 거친 후 기존 식별자를 반환하여 중복 생성을 방지한다.
- **기존 함수 보존**:
  - `tests/integration/rag/test_actual_retrieval_bootstrap.py` 등에서 소비하는 #273 전용 `bootstrap_dev_knowledge_index()`의 시맨틱은 그대로 유지하며, 신규 분리 구조는 독립 모듈(`ai_worker/tasks/evaluation/ret_h_bootstrap_stages.py`)로 제공한다.

### B. Smoke 전용 Synthetic Fixture 분리 및 식별자 규칙

기존 #273 100건 dev 코퍼스 파일(`evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json`)은 변경하지 않는다. RET-H AWS smoke 전용의 독립된 최소 fixture를 도입한다.

- **식별자 및 컬럼 길이 제약 준수**:
  - `source_code`: `SYNTHETIC_RET_H_SMOKE` (21자, `rag_source.source_code` varchar(100) 제약 준수)
  - `endpoint_code`: `SYNTHETIC_RET_H_SMOKE_ENDPOINT` (30자, varchar(100) 준수)
  - `operation_code`: `SYNTHETIC_RET_H_SMOKE_OPERATION` (31자, varchar(100) 준수)
  - `index_code`: `rag-ret-h-aws-smoke-synthetic-index` (35자, `rag_knowledge_index.index_code` varchar(120) 제약 준수)
  - `index_version`: `1.0.0` (5자, varchar(64) 준수)
- **Sentinel 불변성 및 정적 포함 원칙**:
  - PR #683의 사전 검증 요건인 `query_sentinel in synthetic_query`와 `source_sentinel in chunk_text`를 만족하기 위해, sentinel 문자열은 synthetic fixture JSON 파일 내용 자체에 정적으로 포함된다.
  - 실행 시점에 텍스트를 조작하거나 sentinel을 동적으로 덧붙이는(append) 행위는 엄격히 금지된다. 정적 fixture 원본 파일의 SHA-256 해시를 manifest 및 DB snapshot canonical_checksum과 엄격히 일치시킨다.
- **프로덕션 런타임 공유**:
  - Smoke 실행 시 별도의 검색 스택이나 모의(Mock) 루틴을 생성하지 않고, 실제 프로덕션 검색 어댑터, RRF 퓨전 알고리즘, 자격 검증 게이트를 그대로 호출한다.

### C. Synthetic Index 명시적 Allowlist (Exact Match Only)

PR #683의 synthetic 안전 장치(`verify_fixture_is_synthetic`)를 접두사(`startswith("rag-")`)나 와일드카드로 완화하지 않는다.

- `ai_worker/tasks/evaluation/ret_h_smoke.py` 내 허용 인덱스 코드는 불변 frozenset을 통해 완전 일치(Exact Match)로만 검증한다:
  ```python
  APPROVED_SYNTHETIC_INDEX_CODES: frozenset[str] = frozenset({
      SYNTHETIC_INDEX_CODE,  # "rag-natural-language-retrieval-dev-synthetic-index"
      "rag-ret-h-aws-smoke-synthetic-index",
  })
  ```
- 위 허용 목록에 없는 index code가 smoke 실행 경로로 진입할 경우 즉시 `EvaluationValidationError(EvaluationErrorCode.INVALID_INPUT)`를 발생시켜 비합성(non-synthetic) 데이터에 대한 조회를 원천 차단한다.

### D. 런타임 엔티티(`user`, `ai_job`) 생명주기 및 DB_APP_USER Cleanup

`retrieval_run` 적재를 위한 선행 외래 키 충족 및 테스트 후 정리는 오직 런타임 계정(`DB_APP_USER`)의 권한 범위 내에서 수행한다.

- **권한 확인**:
  - `infra/python/provision_database_roles.py`상 `user`와 `ai_job`은 `RUNTIME_MUTABLE_TABLES`에 속해 있으며, `DB_APP_USER`에게 `SELECT, INSERT, UPDATE, DELETE` 권한이 부여되어 있다.
- **엔티티 생성 제약 준수**:
  - `User`: synthetic 전용 이메일(`synthetic-ret-h-smoke-<uuid>@internal.invalid`), `is_active=False`, `is_admin=False`로 최소 유효 행 생성.
  - `AiJob`:
    - `job_type="OCR"` 사용: `chk_ai_job_prescription_version_by_type` 제약 조건 `(job_type = 'OCR' AND prescription_version_id IS NULL)`을 만족하므로 불필요한 `prescription_version` 외래 키 의존성이 발생하지 않는다.
    - `status="PENDING"`, `max_attempts=3`, `attempt_count=0`, `expected_event_id=NULL`.
    - 신규 `job_type`이나 신규 `status`를 추가하지 않고 기존 도메인 모델을 재사용한다.
- **비동기 큐 완전 차단**:
  - Redis Stream(`oryak:jobs`)에 인큐하지 않는다.
  - `outbox_event` 행을 생성하지 않는다.
  - 따라서 백그라운드 `ai-worker`가 해당 테스트 job을 획득(claim)하거나 실행할 위험이 전혀 없다.
- **Fail-Closed 연쇄 삭제 (Cleanup via Cascade)**:
  - `backend/alembic/versions/178c2d3e4f50_create_retrieval_run_tables.py` 및 `backend/app/models/rag_retrieval.py`에 정의된 DB 외래 키 제약 조건:
    * `retrieval_run.job_id` → `ai_job.id` (`ON DELETE CASCADE`)
    * `retrieval_signal.retrieval_run_id` → `retrieval_run.id` (`ON DELETE CASCADE`)
    * `retrieval_hit.retrieval_run_id` → `retrieval_run.id` (`ON DELETE CASCADE`)
  - Cleanup 실행 단계:
    1. `DELETE FROM ai_job WHERE id = :job_id`: PostgreSQL 외래 키 정의에 따라 `retrieval_run`, `retrieval_signal`, `retrieval_hit`가 연쇄 자동 삭제된다.
    2. `DELETE FROM "user" WHERE id = :user_id`: 자식 `ai_job`이 이미 삭제되었으므로 외래 키 충돌 없이 삭제 완료된다.
  - Cleanup은 반드시 `try...finally` 블록에서 `DB_APP_USER` 엔진으로 수행된다.
  - Admin/Migration/Writer/Builder 계정을 cleanup에 사용하지 않는다.
  - Cleanup 실패 시 에러를 은폐하지 않고 즉시 예외를 전파하여 smoke 결과를 FAILED로 종결한다.

---

## 3. 12대 리스크 점검 및 완화 전략

### 리스크 1: PR #683 unmerged 상태에서 구현 착수 위험
- **영향**: PR #683의 review comments 및 base 변경 사항이 반영되지 않은 상태에서 #684 코드를 병합할 경우 충돌 및 재작업이 발생한다.
- **완화책**: `IMPLEMENTATION_GATE` 정책을 적용하여 `PR_683_MERGED: true` 및 `DECISION_APPROVED: true`가 충족되기 전까지 구현 코드 작성을 차단한다.

### 리스크 2: #273 synthetic corpus hash 불일치 위험
- **영향**: 기존 100건 dev corpus를 수정할 경우 기존 회귀 테스트 및 데이터셋 manifest 해시 불일치가 발생한다.
- **완화책**: 기존 dev fixture 파일은 바이트 단위로 보존하고, RET-H smoke 전용 fixture(`SYNTHETIC_RET_H_SMOKE`)를 별도 경로에 신규 생성한다.

### 리스크 3: 기존 `bootstrap_dev_knowledge_index()` 훼손 위험
- **영향**: 기존 통합 테스트(`test_actual_retrieval_bootstrap.py` 등)가 깨질 수 있다.
- **완화책**: 기존 함수의 인터페이스와 동작은 그대로 보존하고, 신규 2단계 권한 분리 로직은 독립된 모듈로 작성하여 smoke 파이프라인에서 호출한다.

### 리스크 4: DB role grant 불필요 확대 위험
- **영향**: 편의를 위해 `SOURCE_WRITER`에 Knowledge 테이블 권한을 주거나 `DB_APP_USER`에 Source/Knowledge 쓰기 권한을 주면 최소 권한 모델이 붕괴된다.
- **완화책**: `infra/python/source_role_policy.py` 및 `knowledge_index_role_policy.py`에 정의된 현행 grant를 절대 확장하지 않으며, CI 계약 테스트(`test_database_role_deployment.py`)로 권한 격리를 검증한다.

### 리스크 5: Admin/Migration credential 런타임 누출 위험
- **영향**: 관리자 비밀번호가 일반 컨테이너 프로세스 환경변수로 노출될 경우 보안 침해 사고 위험이 있다.
- **완화책**: Stage 1, Stage 2 컨테이너에는 각각 `SOURCE_WRITER`와 `KNOWLEDGE_INDEX_BUILDER` 자격 증명만 주입하고, `DB_ADMIN_PASSWORD`와 `DB_MIGRATION_PASSWORD`는 전달하지 않는다.

### 리스크 6: synthetic user/ai_job 잔여물로 인한 DB 오염 위험
- **영향**: 테스트 실행 후 남겨진 행이 운영 통계 집계나 사용자 조회 쿼리에 노출될 수 있다.
- **완화책**: Smoke 실행 runner에 `try...finally` 구조의 cleanup을 의무화하고, `ai_job` 삭제 시 `retrieval_run` 연쇄 삭제가 완료된 후 `user`를 삭제하여 잔여 행을 남기지 않는다.

### 리스크 7: worker가 synthetic ai_job을 집어갈 위험 (enqueue 금지)
- **영향**: 백그라운드 AI Worker가 테스트용 job을 claim하여 비정상 처리하거나 DLQ로 전송할 수 있다.
- **완화책**: `ai_job`을 DB에 직접 INSERT하되 Redis Stream(`oryak:jobs`) 발행과 `outbox_event` 생성을 일체 수행하지 않는다.

### 리스크 8: synthetic allowlist 완화 시 non-synthetic index 오탐 위험
- **영향**: 와일드카드 검증으로 인해 실제 운영 데이터 인덱스가 smoke 대상에 포함될 위험이 있다.
- **완화책**: 인덱스 코드 검증 시 접두사 매칭을 금지하고 `frozenset` 기반의 정확한 문자열 완전 일치(Exact Match)만 허용한다.

### 리스크 9: sentinel append 방식 도입 시 fixture 불일치 위험
- **영향**: 런타임에 텍스트에 sentinel을 덧붙이면 원문 해시가 깨지고 snapshot verification seal 검증이 실패한다.
- **완화책**: Sentinel 문자열을 fixture 원본 JSON에 정적으로 기록하여 canonical checksum 일치성을 보장하고 런타임 문자열 수정을 금지한다.

### 리스크 10: cascade delete 실패 시 foreign key constraint violation 위험
- **영향**: 외래 키 순서 위반으로 cleanup이 실패하여 DB에 잔류 데이터가 고착될 수 있다.
- **완화책**: DDL에 명시된 `ON DELETE CASCADE` 경로에 맞추어 `ai_job`을 먼저 삭제(연쇄하여 `retrieval_run`, `retrieval_signal`, `retrieval_hit` 삭제 유도)한 뒤 부모인 `user`를 삭제하는 엄격한 순서를 준수한다.

### 리스크 11: Docker Compose profile 분리 실패 시 불필요 컨테이너 상시 실행 위험
- **영향**: 일회성 색인 빌더 컨테이너가 배포 시 상시 기동되어 리소스를 점유하거나 재시작 루프에 빠질 수 있다.
- **완화책**: Stage 1(`source-writer`) 및 Stage 2(`knowledge-index-builder`) 서비스를 명시적인 Docker Compose profiles(`source-admin`, `knowledge-index-admin`)로 격리하고 `restart: "no"`를 지정한다.

### 리스크 12: AWS 배포 환경과 local docker compose 간 환경변수 불일치 위험
- **영향**: 로컬에서는 통과하나 AWS 배포 호스트에서 필수 환경변수 누락으로 기동 실패할 수 있다.
- **완화책**: `envs/example.prod.env`에 `KNOWLEDGE_INDEX_BUILDER_USER`, `KNOWLEDGE_INDEX_BUILDER_PASSWORD` 명세를 사전에 반영하고, Compose 환경변수 allowlist에 정확히 일치시킨다.

---

## 4. 구현 시 고려 사항 (Implementation Considerations)

1. **`retrieval_run` 테이블의 DB Role 매핑 점검 (해소 완료)**:
   - 본 Decision 승인 리뷰(`@phina-io`)의 지침에 따라, Runtime ACL parity 문제는 #684에서 분리되어 Issue #689 / PR #698(`66b91479`)에서 전용 해결 및 병합 완료되었다.
   - `retrieval_run` 테이블은 `RUNTIME_RETRIEVAL_RUN_TABLES`로서 `SELECT, INSERT, UPDATE` 권한이 부여되었고, `retrieval_signal` 및 `retrieval_hit`는 `RUNTIME_APPEND_ONLY_TABLES`로서 `SELECT, INSERT` 권한이 `DB_APP_USER`에게 프로비저닝된다.
   - 따라서 본 #684 구현 시에는 DB role grant를 일체 수정하지 않고 기존의 least-privilege role matrix를 온전히 활용한다.
   - 아울러 본 구현 상태에서도 `PUBLIC_TRACK_F_ENABLED=false` 릴리스 게이트는 엄격히 유지된다.

2. **Stage 2의 Source Snapshot Seal 검증 방식**:
   - `infra/python/knowledge_index_role_policy.py`에서 `KNOWLEDGE_INDEX_SOURCE_READ_TABLES`는 `frozenset(SOURCE_TABLES) - {"rag_source_snapshot_verification"}`로 정의되어 있어 `KNOWLEDGE_INDEX_BUILDER`는 `rag_source_snapshot_verification` 테이블을 SELECT할 수 없다.
   - 따라서 Stage 2의 Preflight는 `rag_source_snapshot` 테이블의 `verification_status == 'CURRENT'`, `verification_seal_id IS NOT NULL`, `canonical_checksum` 일치 여부를 검증하는 방식으로 구현하여 기존 DB role 정책을 수정 없이 완벽히 준수한다.

---

## 5. 결론 및 승인 내역

본 문서는 Issue #684의 모든 아키텍처 및 권한 분리 요구사항을 정의하고 단일 책임 리뷰어의 승인을 득하였다.

- **승인자**: 송은영 (`@phina-io`, Backend / Data & Security Technical Controls)
- **승인 일시**: 2026-09-16T23:47:23Z
- **승인 코멘트**: https://github.com/AI-HealthCare-05/AH_05_04/issues/684#issuecomment-5706227386
- **선행 조건**: PR #683 병합 완료, Issue #689 (PR #698) 병합 완료.
- **구현 상태**: `IMPLEMENTATION_ALLOWED: true` 충족 확인 후 Issue #684 구현 진행.
