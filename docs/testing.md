# 테스트 전략

## 범위 기준

테스트와 배포 기준은 현재 MVP와 Post-MVP를 구분합니다.

- **현재 MVP**: FastAPI 요청 안에서 OCR(feature flag 기반 LLM 또는 규칙 구조화), 복약 가이드 생성, 복약 챗봇 응답을 완료하는 동기 one-cycle 흐름
- **Post-MVP**: 비동기 AI Worker, OCR LLM의 최소전송·provenance·Worker 확장, MFDS 공식 Identity·Preflight, Rule-first RAG·Citation·Safety, OTC Chat 상호작용과 AI 응답 품질 평가

Post-MVP용 디렉터리나 문서가 저장소에 있더라도 현재 MVP의 구현 완료 또는 배포 조건으로 간주하지 않습니다.

## 테스트 계층

### 현재 MVP

- `backend/app/tests/`: Backend API·서비스·DB, OCR·가이드·챗봇 AI 어댑터 테스트
- `tests/contract/`: 현재 Backend–AI Core 경계 계약. OpenAPI 회귀 테스트는 아직 없음
- `tests/integration/`: 공통 CORS·오류 동작 검증. 현재 기본 CI 명령에는 포함되지 않음
- `tests/e2e/`: 전체 사용자 여정 테스트를 위한 준비 영역이며 현재 자동화된 E2E 테스트는 없음
- `tests/evals/ocr/`: OCR 엔진 검토 자료와 측정 결과

### Post-MVP 준비 영역

- `ai_worker/tests/`: 비동기 Worker 작업 단위 테스트
- `tests/integration/`: Redis·AI Worker를 연결한 통합 테스트
- `evals/`: Candidate Resolver, RAG 검색·생성, Citation·Safety, 처방약–OTC Rule과 배포 게이트

## MVP 핵심 시나리오

1. 회원가입·로그인과 인증된 사용자 확인
2. 처방전 업로드
3. 같은 요청 안에서 OCR 실행 및 성공·실패 처리
4. OCR 결과 조회, 사용자 검수·수정 및 확정 처방 생성
5. 확정 처방 기반 복약 가이드 동기 생성·저장·조회
6. 확정 처방 기반 채팅 세션 생성
7. 사용자 메시지 저장, OpenAI 단일 응답 생성, AI 메시지 저장을 한 요청에서 완료
8. 외부 AI timeout·가용성·응답 처리 실패의 안전한 오류 매핑과 민감정보 비노출

현재 프롬프트의 추측 금지, 복용 변경 금지, 정보 부족 시 확인 요청, 응급 도움 우선 안내는 MVP 안전 제약입니다. 다만 이를 별도 평가 데이터셋과 임계값으로 판정하는 **AI 응답 품질 게이트**는 Post-MVP입니다.

필수 동의 수집·철회는 디자인 프로토타입에만 있으며 현재 Backend DTO와 실제 사용자 흐름에는 연결되지 않았습니다. 구현되기 전까지 자동화된 MVP 시나리오로 간주하지 않습니다.

## 현재 자동 검증 범위

GitHub Actions와 `scripts/ci/run_test.sh`는 다음 순서로 PostgreSQL migration과 기본 Python 테스트를 검증합니다.

1. 개발 DB와 분리된 PostgreSQL `test` DB를 새로 생성합니다.
2. 선택한 환경파일의 DB 계정으로 `alembic upgrade head`를 실행합니다.
3. `tests/migration/`에서 Alembic이 생성한 실제 PostgreSQL 스키마를 검증합니다.
4. Backend·공통 계약·Worker 공통 테스트를 실행합니다.
5. Coverage 결과를 확인합니다.

로컬 기본 실행 명령은 다음과 같습니다.

```bash
bash scripts/ci/run_test.sh
```

### test runner가 격리하는 설정

`run_test.sh`는 `uv run --env-file`로 `envs/.local.env` 전체를 주입하지만, 그 파일은 컨테이너용이라 host 실행에서 달라야 하는 값을 아래와 같이 덮어씁니다. uv가 shell 환경변수를 `--env-file`보다 우선 적용하는 성질을 사용합니다.

| 설정 | test 실행 값 | 격리하는 이유 |
| --- | --- | --- |
| `DB_HOST`·`DB_PORT`·`DB_EXPOSE_PORT`·`DB_NAME` | loopback과 `test` DB | 개발 DB를 사용하지 않습니다 |
| `DB_USER`·`DB_PASSWORD` | 환경파일 값 사용(shell 값 제거) | 실행자 shell의 계정이 섞이지 않게 합니다 |
| `STORAGE_DIR` | 실행마다 새로 만든 host 임시 디렉터리 | 환경파일 값은 컨테이너 절대경로라 host에 없거나 쓸 수 없습니다 |
| `RELEASE_VALIDATION_ALLOWED` | `false` | local live 검증 절차가 켜두도록 안내하는 gate입니다 |
| `OCR_STRUCTURE_LLM_ENABLED` | `false` | 위와 같습니다. 켜진 값이 필요한 테스트는 각자 `monkeypatch`로 설정합니다 |

그 외 값은 환경파일을 그대로 따릅니다. 위 목록은 `tests/contract/test_run_test_env_isolation.py`가 고정하므로, 새로 격리해야 할 설정이 생기면 그 테스트도 함께 갱신합니다.

기본 자동 검증 범위와 별도 검증 항목은 다음과 같습니다.
- `backend/app/tests/chat_integration/`을 포함한 `backend/app/` 아래 테스트는 기본 실행 범위에 포함됩니다.
- `ai_worker/tests/core/`의 구현된 Worker 공통 단위 테스트는 기본 실행 범위에 포함됩니다.
- `tests/integration/`, `tests/e2e/`, `ai_worker/tests/ocr/`, `ai_worker/tests/rag/`, `ai_worker/tests/llm/`, `ai_worker/tests/evaluation/`과 Frontend 테스트는 기본 실행 범위에 포함되지 않습니다.
- OpenAPI endpoint 목록은 현재 문서 검토로 대조하며 자동 contract regression test에는 연결되지 않았습니다.
- Frontend는 별도로 `pnpm lint`와 `pnpm build`를 실행합니다.
- 가이드 실호출은 `RUN_OPENAI_SMOKE=1`, 챗봇 실호출은 `RUN_OPENAI_CHAT_SMOKE=1`일 때만 실행됩니다. 기본 CI에서 skip되므로 배포 기록에는 별도 실행 결과를 남깁니다.

### Guide AI v3 Local 검증

`guide-prompt-v3`는 실제 Provider 호출 없이 다음 결정론적 검증을 수행합니다.

```bash
uv run pytest backend/app/tests/guide_ai -q
uv run pytest backend/app/tests/guide_ai/test_v3_eval.py -q
```

- intent 분류: `timing_text`가 있으면 `FOLLOW_CONFIRMED_TIMING`, 없으면 필수 frequency 기반 `FOLLOW_CONFIRMED_SCHEDULE`
- 비정상 frequency 누락의 Provider 호출 전 실패
- Provider payload의 `source_index + guidance_intent` 필드 allowlist와 원본 처방값·식별자 비포함
- 구조화 출력의 index 중복·누락·범위 밖 값 및 intent 누락·변경·불일치 차단
- intent별 전체 승인 guidance와 전체 공통 notice 허용, NFC+trim 이후 exact membership 밖 문장 차단
- 숫자·의료 주장·처방 변경·마크업 validator 회귀
- renderer의 원본 처방값·불완전 용량 안내와 검증된 AI guidance 입력 순서 결합
- `prompt_version == guide-prompt-v3`와 로그·오류의 처방값 비노출

버전된 비식별 합성 평가셋은 `evals/generation/guide-v3-eval-v1.json`이며 `data_classification=SYNTHETIC`으로 고정합니다. 이 평가는 자유 생성 품질이나 실제 Provider 응답을 측정하지 않고 승인 문구 선택·안전 차단 계약을 재현합니다. 별도 승인 없이 `RUN_OPENAI_SMOKE=1`을 설정하지 않으며, skip된 실호출 테스트를 성공으로 해석하지 않습니다.

### Chat AI v2 최근 대화 Local 검증

실제 Provider 호출 없이 다음 결정론적 테스트로 최근 대화 조회와 단일 `chat-prompt-v2` 계약을 검증합니다.

```bash
uv run pytest backend/app/tests/chat backend/app/tests/repositories/test_chat_repository.py backend/app/tests/chat_ai backend/app/tests/chat_integration tests/contract/test_chat_ai_backend_contract.py -q
```

- 완료 대화 0·1·3·4쌍, 최신 3쌍 선택과 오래된 순서 전달
- 답변 없음·FAILED·PENDING·GENERATING·비연속 pair 제외와 최대 30개 후보·12,000자 예산
- 현재 질문 중복 제외와 다른 사용자·세션·처방 소유권 경계
- flag OFF의 조회 생략·`history: []`와 flag ON Local 합성 history 전달
- flag와 history 유무에 관계없는 `prompt_version == chat-prompt-v2`
- JSON 문자열을 지시가 아닌 데이터로 취급하는 프롬프트 인젝션 방어
- 과거 USER의 부정확하거나 오래된 증상·진단·알레르기·복용 여부를 현재 사실로 단정하지 않고, 안전상 중요하면 현재도 해당하는지 확인하는 프롬프트 규칙
- 과거 ASSISTANT 비신뢰, 현재 확정 medications 우선과 기존 응답·오류 회귀

`chat-v2-history-eval-v1` 결정론적 Local replay는 [Issue #129](https://github.com/AI-HealthCare-05/AH_05_04/issues/129)에서 추가했습니다. 기준선은 `chat-prompt-v2 + history=[]`, 처리 경로는 동일한 `chat-prompt-v2 + 합성 history`이며 실제 `ChatGenerator`를 통과합니다. 2026-09-01 실행에서 계약 scorer는 기준선·history 각각 10/10, 단일 질문 회귀 1/1, 안전 rule 위반 0건이었습니다. 표본이 평가 축별 30건 미만이므로 품질 비율 임계값은 `NOT_APPLICABLE_SAMPLE_LT_30`입니다.

최대 3쌍·12,000자 입력을 30회 실행한 결정론적 application-path 관찰값은 payload 36,217 bytes, p95 0.059 ms였습니다. 이 값은 즉시 응답하는 replay Provider를 사용한 해당 Local 실행의 메시지 조립·검증 시간이며 실제 네트워크·Provider latency가 아닙니다. 승인된 Provider tokenizer가 없어 token 수는 `NOT_RUN`입니다. 합성 PII sentinel은 허용된 `history[].question`·`answer`에서 2회, payload의 다른 필드·instructions·응답·로그·오류·결과 metadata에서 0회였고, trace pipeline이 없어 trace는 `NOT_APPLICABLE_NO_TRACE_PIPELINE`입니다.

```bash
cd backend
uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --output ../evals/results/chat-v2-history-eval-v1-local-deterministic.json
```

실제 OpenAI 평가는 명시적 Local opt-in을 요청하지 않아 `NOT_RUN`입니다. `RUN_OPENAI_CHAT_HISTORY_EVAL=1`, `ENV=local`, 공백이 아니고 저장소 placeholder와 일치하지 않는 `OPENAI_API_KEY`가 모두 없으면 live runner가 실행을 거부합니다. live 모드는 canonical `chat-v2-history-eval-v1` 경로, `dataset_id`, `SYNTHETIC` 분류와 고정 SHA-256이 모두 일치하는 경우만 허용하며, 임의 `--dataset` 또는 변경된 fixture는 OpenAI client 생성 전에 거부합니다. SHA-256 입력은 CRLF를 LF로 정규화해 Windows와 Unix checkout을 동일하게 처리하고, CRLF 상태에서도 fixture 내용 변경은 거부하는 회귀 테스트를 유지합니다. 결정론적 결과는 PR #128 또는 Production 공개·Privacy 승인 근거가 아닙니다.

### MVP 공통 오류·no-store 회귀

PR #107 이후 현재 MVP API는 공통 오류 envelope와 `/api/v1/*` `Cache-Control: no-store` 정책을 회귀 테스트로 고정합니다.

- 등록되지 않은 `/api/v1/*` 경로의 기본 404와 지원하지 않는 HTTP 메서드의 기본 405는 `{"code","message","details","trace_id"}` 오류 envelope를 반환합니다.
- Pydantic 요청 검증 실패는 `422 VALIDATION_FAILED`이며 `details`는 객체가 아니라 배열입니다.
- 인증·사용자·처방·의료문서·OCR·가이드·채팅 API의 성공 응답과 4xx/5xx 오류 응답은 모두 `Cache-Control: no-store`를 포함합니다.
- Router endpoint와 FastAPI/Starlette 예외 처리 계층까지 도달하지 않고 최외곽 CORS middleware가 직접 처리하는 preflight 응답은 `/api/v1/*` 공통 오류 envelope와 `no-store` 검증 범위에서 제외합니다.
- 처방 확정과 OCR 검수 오류 응답의 `message`와 `details[].rejected_value`에는 OCR `raw_value`, 처방 원문, Provider 원문 오류, 챗봇 질문·답변, 비밀번호·토큰을 넣지 않습니다.

## MVP 배포 차단 기준

- Ruff·Mypy·현재 범위의 자동 테스트 실패
- OpenAPI, DTO와 실제 응답 계약 불일치
- 미확정 OCR 값을 확정 처방으로 사용
- 확정 처방과 가이드의 결정론적 복약 정보 불일치
- timeout·외부 서비스 실패·잘못된 AI 응답을 성공으로 저장 또는 반환
- 로그나 오류 응답에 처방전 원문, 사용자 질문, API Key 등 민감정보 노출
- `SECURITY.md`의 근거·검증 추적 원칙을 충족하지 못함. 승인표나 수동 검토만으로 예외 처리하지 않음
- 실제 OpenAI 호출 확인 및 운영 설정 승인을 포함한 `docs/deployment.md`의 배포 기록 미완료

현재 자동화되지 않은 검증 항목은 통과한 것으로 간주하지 않습니다. 배포 차단 기준에 포함하려면 수동 확인 결과를 배포 기록에 남기거나 CI에 검증 명령을 연결해야 합니다.

## Post-MVP 품질 게이트

다음 항목은 기능 구현, 데이터셋·임계값 합의, 재현 가능한 평가 실행이 완료된 뒤 배포 차단 기준으로 전환합니다.

- RAG 검색 Recall@K와 승인 지식 소스·인덱스 버전 검증
- 주요 의료 주장 Citation coverage와 출처 추적
- 결정적 Claim–Citation 완전성, Source·locator 유효성과 근거 범위 검증
- 응급·고위험·정보 부족 사례의 AI 안전 평가
- MFDS Candidate Resolver·Single Candidate Gate와 처방약–OTC Rule·Evidence·정보 부족 fallback 평가
- 모델·프롬프트·검색 인덱스 변경에 대한 회귀 평가
- 고정 CLOVA 원본 응답 fixture를 Provider adapter에 재생하는 결정론적 OCR 회귀 검증

위 평가 체계가 Post-MVP라는 분류는 현재 의료 안전 원칙을 유예한다는 뜻이 아닙니다. 구현 전 사용자 검증은 비식별 합성 데이터와 접근 통제를 사용하는 내부 staging 데모로 제한하며 Production 승인으로 간주하지 않습니다.

## Post-MVP-1 목표 계약 테스트 — 미구현

다음 항목은 목표 계약의 완료 조건이며 현재 CI에서 통과한 것으로 간주하지 않습니다. 관련 기능 PR은 구현·OpenAPI·migration과 함께 해당 테스트를 추가하고 실제 실행 결과를 남겨야 합니다.

- 동일 멱등 키·동일 요청은 Job을 하나만 만들고 기존 Job의 최신 `202`를 반환합니다.
- 동일 멱등 키·다른 요청은 `409 IDEMPOTENCY_KEY_CONFLICT`입니다.
- OCR·Guide·Chat 접수 `202 Accepted` 응답은 `{"data": JobStatusResponse}` envelope와 `Location = data.status_url`을 함께 반환합니다.
- `GET /api/v1/jobs/{job_id}`는 `{"data": JobStatusResponse}` envelope를 반환하고, `RETRY_WAIT`에서는 `Retry-After`와 `retry_after_seconds`가 같은 값입니다. `#148`의 `test_job_status_api.py`로 실제 HTTP 요청(인증, 소유권 404, `COMPLETED`/`STALE`의 `result_url` 노출 기준, `Cache-Control: no-store`) 기준 검증 완료. OCR·Guide·Chat 접수 자체의 `202` 응답은 접수 API가 아직 `accept_job()`에 연결되지 않아 이 범위 밖입니다.
- Job 접수·상태 조회·결과 조회의 성공 응답과 `400`, `401`, `404`, `409`, `500`, `503` 오류 응답은 모두 `Cache-Control: no-store`를 포함합니다.
- Job 접수·상태 조회 오류 응답은 공통 오류 envelope를 사용하고 `details`를 객체가 아니라 배열로 반환합니다.
- HMAC key rotation 중 미만료 record가 존재할 수 있는 모든 retained key version 조회와 혼합 writer 차단 또는 rotation-invariant 원자 잠금으로 같은 원문 key의 중복 실행을 방지합니다. 현재·직전 key version만 조회하는 구현은 rotation 주기가 최대 멱등 레코드 보존기간보다 길 때만 허용합니다.
- 접수 transaction 실패 시 Job·Outbox·placeholder·멱등 레코드가 함께 rollback됩니다. Service·Repository 계층은 `#147`의 `test_job_intake.py`(`test_accept_job_rolls_back_all_records_when_document_not_owned_by_user` 등)로 real Postgres 기준 검증됐습니다. API 라우트가 없어 실제 `202`/오류 응답 형태 확인은 `#148`에서 이어집니다.
- 같은 멱등 키 동시 접수는 DB unique constraint로 하나만 Job을 생성하고 나머지는 기존 Job의 최신 `202`를 반환합니다. DB unique constraint 기반 동시성 처리는 `#147`의 `test_accept_job_concurrent_same_key_creates_only_one_job`으로 검증됐고, 실제 `202` 응답은 `#148`에서 확인합니다.
- 비동기 요청은 `record_type + user_id + operation_id + key_hmac`, 동기 요청은 `record_type + user_id + operation_id + parent_resource_id + key_hmac` unique 기준으로 동시 중복 생성을 차단합니다.
- 만료된 멱등 row 정리와 새 Job 생성은 중복 Job·Outbox·Provider 호출을 만들지 않습니다. Service·Repository 계층의 원자적 reclaim(만료 row 삭제 후 새 Job 생성, 경쟁 시 기존 unique constraint 재조회 경로로 합류)은 `#147`의 `test_accept_job_expired_record_is_reclaimed_and_creates_new_job`으로 검증됐습니다. 실제 `202` 응답은 `#148`에서 확인합니다.
- 중복 전달과 Worker 재시작에도 결과 side effect는 한 번만 반영되고 DB commit 전에는 ACK하지 않습니다.
- Publisher가 `CLAIMED` Outbox row 선점 뒤 종료하면 claim 만료 후 같은 Outbox row를 재선점하며, Reconciler가 미발행 `PENDING` Job에 대해 새 attempt Outbox를 만들지 않는지 검증합니다.
- Worker 종료 후 lease가 만료된 `PROCESSING` Job은 Reconciler가 회수해 재시도 가능하면 `RETRY_WAIT`, 재시도 소진이면 `FAILED`로 전환하며, 새 Provider 호출은 증가한 attempt의 새 Outbox 이후에만 발생합니다.
- poison 메시지는 quarantine 기록을 먼저 commit한 뒤 ACK하며, commit 실패 시 ACK하지 않아 다시 회수할 수 있어야 합니다.
- poison 메시지에서 파싱한 `job_id`만으로 정상 Job을 `FAILED` 처리하지 않습니다. 실제 Outbox event, `expected_event_id`, Job-event 연결과 attempt 검증이 모두 성공한 경우에만 Job 상태를 변경합니다.
- 만료된 lease의 Worker가 새 Worker의 결과를 덮어쓰지 못합니다.
- lease 만료로 같은 Stream 메시지를 재획득해도 같은 attempt에서 Provider를 반복 호출하지 않습니다.
- `available_at`이 지난 `RETRY_WAIT` Job은 Reconciler가 DB row claim과 unique 제약으로 후속 Outbox를 하나만 생성합니다.
- Publisher가 `CLAIMED` Outbox row 선점 뒤 종료해도 만료된 claim을 재선점할 수 있으며, 발행 완료 갱신은 `claim_token` fencing으로 보호됩니다.
- OCR의 CLOVA 20초·구조화 LLM 30초 순차 호출 경계에서 `hard timeout 60초 / lease 75초`를 검증하고, timeout 직전 정상 결과가 재시도 소진으로 오분류되지 않는지 확인합니다.
- 단일 `idempotency_record`의 `record_type=ASYNC_JOB|SYNC_MUTATION`, 타입별 nullability CHECK, 동기 snapshot `BYTEA` 암호화와 비동기 snapshot 미저장을 계약·migration 테스트로 검증합니다.
- Outbox 30일 보존과 Job 90일 보존이 충돌하지 않도록 nullable FK, `ON DELETE SET NULL` 또는 삭제 전 참조 해제 기준을 migration 테스트로 검증합니다. Outbox 삭제는 연결된 Job이 terminal이고 관련 Stream entry, PEL, 예약 retry와 재발행 대상이 모두 정리된 경우에만 허용합니다.
- `RETRY_WAIT` 중 active Runtime Bundle이 변경된 Job과 구·신 Worker가 함께 실행되는 배포를 검증합니다. 기대 전이와 Worker–Bundle 호환성 규칙이 재승인되기 전에는 이 행을 `NOT_RUN`으로 유지합니다.
- 처방 active version 변경 시 처리 중 결과는 `STALE`이며 현재 결과로 노출되지 않습니다.
- 같은 Chat session의 다른 키 요청은 `409 CHAT_JOB_IN_PROGRESS`이고 동일 키 재전송은 기존 Job을 반환합니다.
- Check-in의 `TAKEN`, `NOT_TAKEN`, `UNCONFIRMED`와 Barrier 거절·미제출을 구분합니다.
- Track B 일정 생성·변경에서 `frequency_per_day`가 존재하면 `local_times.length`와 반드시 일치해야 하며, 불일치 시 422로 실패하고 schedule row와 schedule time row를 저장하지 않습니다.
- Track B `setup_reason` 신규 값·우선순위는 Proposed/TBD이며 별도 Decision 또는 Contract Freeze version 승인 전에는 완료 조건이나 확정 테스트 기대값으로 사용하지 않습니다. 승인 후에는 Backend 계약 테스트에서 고정 우선순위 계산과 nullable 단일 `setup_reason` 반환을 검증하고, Frontend 테스트에서는 반환값의 표시·분기만 검증하며 우선순위를 재계산하지 않습니다. `NO_ACTIVE_PRESCRIPTION`은 전체 `schedule_status`로만 반환합니다.
- Scheduler deadline 처리와 사용자 `PUT` 최초 생성이 경쟁해도 `medication_checkin.occurrence_id` unique 제약, 조건부 insert와 insert 충돌 처리로 현재 Check-in row가 하나만 생성되는지 검증합니다. 사용자 최초 생성의 `expected_revision`은 `0`입니다.
- `reason_code`는 enum 확정 전까지 Check-in 생성·정정 요청, OpenAPI request schema, DB enum과 `checkin_audit` 컬럼에 포함하지 않습니다. 테스트 fixture의 예시값도 확정 enum처럼 사용하지 않습니다.
- 처방 version 변경 시 새 version 일정을 자동 복사·자동 생성하거나 이전 일정을 참고 후보로 제공하지 않습니다. 이전 version과 값이 같아도 새 version의 모든 medication을 재확인 전 `SETUP_REQUIRED`로 반환합니다.
- version `effective_at` 이후의 이전 version `PENDING` occurrence와 미전달 알림만 취소합니다. 이전 schedule·time revision, `effective_at` 이전 occurrence, Check-in·audit은 원래 version에 보존하고, deadline이 지난 과거 `PENDING` occurrence는 취소가 아닌 `UNCONFIRMED` 생성 대상으로 검증합니다.
- 처방 version 확정과 이전 occurrence·미전달 알림 취소의 transaction·Outbox 결합 방식은 Track A 비동기 인프라와 후속 Decision 전까지 `NOT_RUN`으로 유지합니다.
- `UNCONFIRMED` backlog 조회·다음 로그인 보완 Flow는 Track B 완료 조건으로 검증합니다. 전용 API의 URL·pagination 계약은 후속 Issue에서 고정할 수 있지만 이 기능 자체를 완료 범위에서 제외하지 않습니다.
- 사용자 알림 ON/OFF preference와 외부 Push·SMS 채널 정책은 후속 Issue로 분리할 수 있습니다. 앱 내부 알림의 생성·중복 방지·version 변경 시 취소와 알림 상태로 복용 결과를 추정하지 않는 기준은 Track B 완료 조건으로 검증합니다.
- Track B의 Check-in write가 Track C의 `invalidate_for_checkin_revision`을 별도 queue·Outbox 없이 같은 transaction의 in-process 동기 함수로 호출하고, 정의된 `MEDICATION_CHECKIN → SAFETY_ASSESSMENT → BARRIER_RESPONSE → SUPPORT_ACTION_PLAN` 잠금 순서와 rollback 경계를 지키는지 검증합니다.
- 다른 사용자의 Job·결과와 Track B occurrence·Check-in, Track C Safety·Barrier·ActionPlan, Candidate·Identification·Chat session 직접 요청은 `404`이며 Redis·일반 로그·quarantine·DLQ에는 의료 원문을 저장하지 않습니다.
- #117 병합 이후 `SELF profile` 이관은 current 계약 기준으로 검증합니다. 의료문서·처방·가이드·채팅 세션은 `profile_id` 또는 부모 chain의 `profile_id`로 소유권을 확인하고, 다른 사용자의 리소스 접근은 `404`로 숨깁니다.
- `AI_JOB_ATTEMPT.BLOCKED` enum은 승인 schema에 남기되 의미·기록 조건·전이 Decision 전에 Worker가 해당 값을 생성하지 않고 `BLOCKED_ACTION`과 연결하지 않는지 검증합니다.
- 근거 없음·상충·timeout·검증 실패는 정상 답변이 아니라 승인된 fallback 또는 공개 차단으로 처리합니다.
- OTC Chat은 미식별·Rule 없음·근거 없음·상충·비활성 Source·Citation 실패에서 안전 보장 문구를 만들지 않고 생성 답변을 폐기한 뒤 승인 fallback으로 종료합니다.

### Track E 비-RAG LLM 구조화

- versioned allowlist만 Provider에 전달하고 원본 이미지, patient name/birth 원문, 내부 사용자·문서·처방 ID와 인증정보가 payload에 없는지 sentinel로 검증합니다.
- Retrieval·RAG·vector search·외부 의료 Source 검색을 호출하지 않습니다.
- raw/rule/LLM draft/user-corrected/confirmed 값과 allowlist·schema·prompt·model·validator version을 서로 덮어쓰지 않습니다.
- timeout·schema·validator·필수값 실패에서 자동 확정과 Prescription·Prescription Version 생성을 차단하고 수동 입력·재시도를 제공합니다.
- Track F Resolver에는 confirmed `medication_name + nullable strength_text`만 전달합니다.

### Track F 공식 Identity·Preflight

- MFDS artifact·Source Snapshot의 checksum, importer·normalization version, 행 수·필수 schema와 원자적 활성화·rollback을 검증합니다.
- Exact→승인 Alias→Trigram·편집거리→OCR 전용 pgvector 순서, 공식 Identity 중복 제거와 Candidate Index version을 재현합니다.
- Single Candidate Gate에서 함량 누락·복수 variant·명시 함량·제형 충돌을 차단하고 외부 후보를 최대 1개로 제한합니다.
- `AMBIGUOUS`에서 내부 1위·Top-K·score를 노출하지 않고 잘못된 자동 `MATCHED`를 0건으로 유지합니다.
- 사용자 확인·거절의 멱등 transaction, 동시 선택 단일 성공, append-only Identification과 소유권을 검증합니다.
- 자동 Guide는 모든 활성 약의 현재 Identification 전에는 Job을 만들지 않고 동기 `REVIEW_REQUIRED`를 반환합니다. Chat은 Identification 전에도 최소 Safety Intake Job을 만들 수 있지만, `ROUTINE`만 Identification Preflight 후 일반 Rule·RAG로 진행합니다. `URGENT | EMERGENCY | UNKNOWN`은 일반 Retrieval·Composer·Provider 호출 0건을 검증합니다.
- 처방·Identification·Source·Runtime Bundle 변경 뒤 과거 결과가 `STALE`인지 검증합니다.

### Track F Evaluation Release Gate

- Release 통합 Experiment Type은 `END_TO_END_RAG`이며 `HOLDOUT`과 `SAFETY_REGRESSION`을 모두 요구합니다. `END_TO_END_FINAL`은 저장하거나 혼용하지 않습니다.
- Critical Safety Failure, Critical Unsupported Claim, Citation 없는 의료 Claim, 미승인·만료 Source, 부적격 Bundle 부분 실행, 처방약–처방약 안전 단정, 음식·음료·보충제 개별 상호작용 판정과 승인 근거 없는 생활습관 행동 제안은 각각 0건이어야 합니다.
- Retrieval Recall@5 90% 이상, Citation Precision·Coverage 각각 95% 이상, 처방약–OTC DUR 양성 Runtime Recall 100%는 초기 목표 예시입니다. RAG-03에서 Baseline·표본·독립 Group·95% 신뢰구간과 versioned Policy를 승인한 뒤에만 Release Threshold로 활성화합니다.
- OCR·Resolver·Candidate 품질과 승인 DUR Source 행→Rule 변환 Coverage는 RAG Metric에 합산하지 않고 별도 Contract Suite의 불변 `COMPLETED/PASS` Receipt로 연결합니다. Receipt가 미구현·미실행·오류이면 RAG 점수와 무관하게 Release를 차단합니다.
- 모든 비율은 분자·분모와 95% 신뢰구간을 기록합니다. 필수 Partition 미실행은 `execution_status=NOT_EVALUATED`, `decision_status=null`입니다. 실행을 완료했지만 분모 0·최소 Case·독립 Group이 부족할 때만 `COMPLETED/INCONCLUSIVE`로 공개를 차단합니다.

### Frontend Job 상태와 재접속 복구

- OCR·Guide·Chat의 공통 상태 UI가 `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE`을 서로 다른 상태로 처리합니다.
- `RETRY_WAIT`에서는 HTTP `Retry-After`와 `retry_after_seconds`가 같은 값인지 확인하고 해당 대기 후 polling을 계속합니다.
- `COMPLETED`에서만 Backend가 제공한 opaque `result_url`로 결과를 조회하며, `STALE` 결과는 현재 결과로 노출하지 않습니다.
- OCR의 `REVIEW_REQUIRED`는 Job 상태가 아니라 `COMPLETED` 결과의 별도 사용자 검수 상태로 처리합니다.
- OCR·Guide·Chat 처리 중 화면 이탈·재접속 후 새 Job을 만들지 않고 기존 `job_id`와 `status_url`로 polling을 복구합니다. Chat에서 Client에 Job 정보가 없으면 ASSISTANT 메시지의 `job_id`로 복구합니다.
- 정상·중복 요청·재시도·`FAILED`·`STALE`·재접속 시나리오를 Frontend fixture와 계약 또는 통합 테스트로 검증합니다.

### Track E OCR 회귀 게이트

Worker 이관 전후 OCR 비퇴행은 CI replay와 release smoke를 분리해 검증합니다.

- **결정적 CI replay:** 승인된 비식별·합성 이미지와 고정 CLOVA 원본 응답 fixture를 Provider adapter에 재생합니다. 동일 입력의 Provider 상태, 필드 mapping, raw·rule-normalized·LLM-draft·user-corrected·confirmed 전이, 검수 필요 판정과 사용자 확정 흐름을 결정적으로 비교합니다.
- **Release smoke:** 승인된 데모 이미지로 실제 CLOVA를 호출합니다. Provider 모델 변경에 따른 비결정적 출력 차이는 기록하고 OCR owner가 검토하되 모든 PR의 exact-equality 차단 조건으로 사용하지 않습니다. critical field 또는 확정 흐름 회귀는 release를 차단합니다.

각 fixture manifest에는 다음을 기록합니다.

- `fixture_id`, 원본 이미지 content hash
- provider와 capture 시각
- `SYNTHETIC` 또는 `APPROVED_DEIDENTIFIED` 분류
- 예상 Provider 상태와 필드
- 예상 raw·rule-normalized·LLM-draft·user-corrected·confirmed 전이
- schema·prompt·model·validator 버전
- normalization version
- 승인자 역할과 승인 시각

실제 환자정보, 재식별 가능한 처방과 인증정보는 fixture에 포함하지 않습니다. Critical field는 약명, 용량, 단위, 복용 횟수, 복용 시점, 기간이며 CI replay의 critical invariant는 100% 통과해야 합니다. 성공·부분 추출·저신뢰·timeout·Provider 오류, 미확정 값의 downstream 유입 차단, 사용자 수정·확정 값 보존과 fixture manifest 완전성을 최소 검증 범위로 둡니다.

Track별 요구사항·계약·소유자·예정 테스트·승인 증빙은 [Post-MVP-1 계약 추적표](./testing/post-mvp-1-contract-traceability.md)에서 연결합니다.
