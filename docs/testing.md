# 테스트 전략

## 범위 기준

테스트와 배포 기준은 현재 MVP와 Post-MVP를 구분합니다.

- **현재 MVP**: FastAPI 요청 안에서 OCR, 복약 가이드 생성, 복약 챗봇 응답을 완료하는 동기 one-cycle 흐름
- **Post-MVP**: 비동기 AI Worker, RAG, 출처 인용, Citation/NLI 검증, OTC 성분·상호작용 기능, AI 응답 품질 평가

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
- `evals/`: RAG 검색, 생성, Citation/NLI, 안전, OTC 품질 평가와 배포 게이트

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

기본 자동 검증 범위와 별도 검증 항목은 다음과 같습니다.
- `backend/app/tests/chat_integration/`을 포함한 `backend/app/` 아래 테스트는 기본 실행 범위에 포함됩니다.
- `ai_worker/tests/core/`의 구현된 Worker 공통 단위 테스트는 기본 실행 범위에 포함됩니다.
- `tests/integration/`, `tests/e2e/`, `ai_worker/tests/ocr/`, `ai_worker/tests/rag/`, `ai_worker/tests/llm/`, `ai_worker/tests/evaluation/`과 Frontend 테스트는 기본 실행 범위에 포함되지 않습니다.
- OpenAPI endpoint 목록은 현재 문서 검토로 대조하며 자동 contract regression test에는 연결되지 않았습니다.
- Frontend는 별도로 `pnpm lint`와 `pnpm build`를 실행합니다.
- 가이드 실호출은 `RUN_OPENAI_SMOKE=1`, 챗봇 실호출은 `RUN_OPENAI_CHAT_SMOKE=1`일 때만 실행됩니다. 기본 CI에서 skip되므로 배포 기록에는 별도 실행 결과를 남깁니다.

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
- Citation/NLI 기반 faithfulness·entailment 검증
- 응급·고위험·정보 부족 사례의 AI 안전 평가
- OTC 성분 식별, 중복·상호작용 탐지와 정보 부족 Fallback 평가
- 모델·프롬프트·검색 인덱스 변경에 대한 회귀 평가
- 고정 CLOVA 원본 응답 fixture를 Provider adapter에 재생하는 결정론적 OCR 회귀 검증

위 평가 체계가 Post-MVP라는 분류는 현재 의료 안전 원칙을 유예한다는 뜻이 아닙니다. 구현 전 사용자 검증은 비식별 합성 데이터와 접근 통제를 사용하는 내부 staging 데모로 제한하며 Production 승인으로 간주하지 않습니다.

## Post-MVP-1 목표 계약 테스트 — 미구현

다음 항목은 목표 계약의 완료 조건이며 현재 CI에서 통과한 것으로 간주하지 않습니다. 관련 기능 PR은 구현·OpenAPI·migration과 함께 해당 테스트를 추가하고 실제 실행 결과를 남겨야 합니다.

- 동일 멱등 키·동일 요청은 Job을 하나만 만들고 기존 Job의 최신 `202`를 반환합니다.
- 동일 멱등 키·다른 요청은 `409 IDEMPOTENCY_KEY_CONFLICT`입니다.
- 접수 transaction 실패 시 Job·Outbox·placeholder·멱등 레코드가 함께 rollback됩니다.
- 중복 전달과 Worker 재시작에도 결과 side effect는 한 번만 반영되고 DB commit 전에는 ACK하지 않습니다.
- poison 메시지는 quarantine 기록을 먼저 commit한 뒤 ACK하며, commit 실패 시 ACK하지 않아 다시 회수할 수 있어야 합니다.
- 만료된 lease의 Worker가 새 Worker의 결과를 덮어쓰지 못합니다.
- 처방 active version 변경 시 처리 중 결과는 `STALE`이며 현재 결과로 노출되지 않습니다.
- 같은 Chat session의 다른 키 요청은 `409 CHAT_JOB_IN_PROGRESS`이고 동일 키 재전송은 기존 Job을 반환합니다.
- Check-in의 `TAKEN`, `NOT_TAKEN`, `UNCONFIRMED`와 Barrier 거절·미제출을 구분합니다.
- 다른 사용자의 Job·결과와 Track B occurrence·Check-in, Track C Safety·Barrier·ActionPlan, Track D OTC evaluation 직접 요청은 `404`이며 Redis·로그·DLQ에는 의료 원문을 저장하지 않습니다.
- 근거 없음·상충·timeout·검증 실패는 정상 답변이 아니라 승인된 fallback 또는 공개 차단으로 처리합니다.
- OTC 평가 불완전 `UNKNOWN`과 승인 범위 내 rule 무성립을 구분하며 안전 보장 문구를 노출하지 않습니다.

### Frontend Job 상태와 재접속 복구

- OCR·Guide·Chat의 공통 상태 UI가 `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE`을 서로 다른 상태로 처리합니다.
- `RETRY_WAIT`에서는 HTTP `Retry-After`와 `retry_after_seconds`가 같은 값인지 확인하고 해당 대기 후 polling을 계속합니다.
- `COMPLETED`에서만 Backend가 제공한 opaque `result_url`로 결과를 조회하며, `STALE` 결과는 현재 결과로 노출하지 않습니다.
- OCR의 `REVIEW_REQUIRED`는 Job 상태가 아니라 `COMPLETED` 결과의 별도 사용자 검수 상태로 처리합니다.
- OCR·Guide·Chat 처리 중 화면 이탈·재접속 후 새 Job을 만들지 않고 기존 `job_id`와 `status_url`로 polling을 복구합니다. Chat에서 Client에 Job 정보가 없으면 ASSISTANT 메시지의 `job_id`로 복구합니다.
- 정상·중복 요청·재시도·`FAILED`·`STALE`·재접속 시나리오를 Frontend fixture와 계약 또는 통합 테스트로 검증합니다.

### Track E OCR 회귀 게이트

Worker 이관 전후 OCR 비퇴행은 CI replay와 release smoke를 분리해 검증합니다.

- **결정적 CI replay:** 승인된 비식별·합성 이미지와 고정 CLOVA 원본 응답 fixture를 Provider adapter에 재생합니다. 동일 입력의 Provider 상태, 필드 mapping, raw·normalized·confirmed 전이, 정규화, 검수 필요 판정과 사용자 확정 흐름을 결정적으로 비교합니다.
- **Release smoke:** 승인된 데모 이미지로 실제 CLOVA를 호출합니다. Provider 모델 변경에 따른 비결정적 출력 차이는 기록하고 OCR owner가 검토하되 모든 PR의 exact-equality 차단 조건으로 사용하지 않습니다. critical field 또는 확정 흐름 회귀는 release를 차단합니다.

각 fixture manifest에는 다음을 기록합니다.

- `fixture_id`, 원본 이미지 content hash
- provider와 capture 시각
- `SYNTHETIC` 또는 `APPROVED_DEIDENTIFIED` 분류
- 예상 Provider 상태와 필드
- 예상 raw·normalized·confirmed 전이
- normalization version
- 승인자 역할과 승인 시각

실제 환자정보, 재식별 가능한 처방과 인증정보는 fixture에 포함하지 않습니다. Critical field는 약명, 용량, 단위, 복용 횟수, 복용 시점, 기간이며 CI replay의 critical invariant는 100% 통과해야 합니다. 성공·부분 추출·저신뢰·timeout·Provider 오류, 미확정 값의 downstream 유입 차단, 사용자 수정·확정 값 보존과 fixture manifest 완전성을 최소 검증 범위로 둡니다.

Track별 요구사항·계약·소유자·예정 테스트·승인 증빙은 [Post-MVP-1 계약 추적표](./testing/post-mvp-1-contract-traceability.md)에서 연결합니다.
