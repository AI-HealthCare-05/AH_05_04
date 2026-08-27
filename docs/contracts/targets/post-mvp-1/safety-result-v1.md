# Safety Result 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Not implemented · C·F 구현 동기화와 지정 리뷰어 검토·외부 승인 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-c-support-v1.md`, `track-f-rag-citation-safety-v1.md` |
| Last verified | 2026-08-27 |

## 공통 원칙

안전 결과는 모델의 자연어만 저장하지 않는다. 입력 처방 버전, 구조화 상태, 검증 결과, 공개 결정, 근거 인용을 함께 저장한다. 근거가 없거나 검증이 실패하면 정상 답변으로 공개하지 않는 fail-closed 규칙을 적용한다.

Track C 직접 API와 Track F Job·결과·Candidate·Identification은 인증 사용자가 직접 소유한 resource만 허용한다. Safety·Barrier는 Check-in parent chain, Track F는 Job·Prescription Version·Prescription Medication parent chain을 통해 같은 `user_id`를 확인한다. 존재하지 않거나 소유하지 않은 식별자는 모두 `404`다. 보호자·patient profile·위임 요청과 별도 운영자 열람 역할은 후속 계약이다.

## Track F 상태 축

| 축 | 값 |
|---|---|
| `response_level` | `ROUTINE`, `URGENT`, `EMERGENCY`, `UNKNOWN` |
| `execution_status` | `SUCCEEDED`, `NO_RESULT`, `TIMED_OUT`, `DEPENDENCY_ERROR`, `VALIDATION_ERROR` |
| `release_decision` | `PASS`, `LIMITED`, `REJECTED`, `STALE` |
| `safety_disposition` | `NORMAL`, `URGENT_ROUTED`, `EMERGENCY_ROUTED`, `BLOCKED_ACTION`, `UNKNOWN_RISK` |

Router와 저장 결과를 혼용하지 않는다. `ROUTINE → NORMAL`, `URGENT → URGENT_ROUTED`, `EMERGENCY → EMERGENCY_ROUTED`, `UNKNOWN → UNKNOWN_RISK`로 매핑하고, 정책 검증이 특정 행동을 차단한 경우에만 `BLOCKED_ACTION`을 저장한다.

정확한 조합은 다음으로 고정한다.

| 상황 | execution | release | safety |
|---|---|---|---|
| 승인된 정상 답변 | `SUCCEEDED` | `PASS` | `NORMAL` |
| 승인된 긴급 안내 | `SUCCEEDED` | `PASS` | `URGENT_ROUTED` |
| 승인된 응급 안내 | `SUCCEEDED` | `PASS` | `EMERGENCY_ROUTED` |
| 확정 처방 사실·고정 한계 안내만 가능 | `SUCCEEDED` | `LIMITED` | `NORMAL` 또는 Router 결과 |
| 근거 없음 또는 근거 충돌 | `NO_RESULT` | `REJECTED` | Router 결과 |
| 생성 시간 초과 | `TIMED_OUT` | `REJECTED` | Router 결과 |
| 의존 서비스 실패 | `DEPENDENCY_ERROR` | `REJECTED` | Router 결과 |
| schema·근거·안전 검증 실패 | `VALIDATION_ERROR` | `REJECTED` | Router 결과 |
| 최신 처방 버전이 아님 | 원래 값 보존 | `STALE` | 원래 값 보존 |

`STALE`이면 `is_current=false`이며 현재 답변으로 노출하지 않는다. `REJECTED` 결과에는 생성된 의료 답변을 노출하지 않고 승인된 고정 fallback만 반환한다. `PASS`, `LIMITED`, `REJECTED`와 fallback이 commit되면 공통 `AI_JOB=COMPLETED`다. timeout·dependency 결과도 fallback을 commit하면 `COMPLETED`이며, fallback조차 저장하지 못한 실행 실패만 `AI_JOB=FAILED`다.

## 저장 모델과 근거

Track F의 `safety_result`는 Guide 또는 Chat Job과 처방 version에 귀속하며 `response_level`, `execution_status`, `release_decision`, `safety_disposition`, `is_current`, 승인 content 또는 fallback을 저장한다. 모델·prompt·validator·Source·Rule·Runtime Bundle version과 Citation을 함께 기록하고 상태축의 허용 조합은 DB 제약 또는 서비스 검증으로 강제한다. 정확한 물리 컬럼명은 migration·DTO·계약 테스트를 포함한 구현 PR에서 확정한다. 동기 처리인 Track C의 도메인 결과를 `AI_JOB`에 귀속하지 않는다.

`fallback_code`는 `NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `UNSUPPORTED_REQUEST`로 제한한다. Job 기반 ASSISTANT 응답은 `job_id`, Job 상태와 동일한 `generation_status`, nullable `content`, `prescription_version_id`, `is_current`, 세 결과 상태축, `response_level`, nullable `fallback_code`, `citations[]`를 제공한다. non-terminal·`FAILED`·`STALE`에서는 `content=null`이며 `COMPLETED`에서만 승인 답변 또는 fallback을 노출한다.

`retrieval_run` 하나에는 여러 retrieved chunk와 score·rank를 연결한다. Citation은 result의 개별 claim과 evidence source를 연결하며 `source_type`, `source_id`, `source_version`, `locator`, `claim_key`를 가진다. `(safety_result_id, claim_key, source_type, source_id, source_version, locator)`를 unique로 둔다. 허용 source type은 `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `SAFETY_POLICY`다. 출처 원문 전체와 검증에서 폐기한 생성 답변은 결과 row나 Redis에 복제하지 않는다.

Citation 공개 DTO는 다음 필드로 제한한다.

- `citation_id`, `claim_key`, `source_type`
- `title`, nullable `url`
- `source_version`, `locator`
- 짧은 표시용 `excerpt`

내부 retrieval score, 검증 세부와 Source 원문 전체는 공개하지 않는다.

RetrievalRun에는 raw 질문을 저장하지 않고 다음 감사 메타데이터만 저장한다.

- versioned query HMAC/digest
- filter snapshot과 `prescription_version_id`
- 검색된 chunk ID, rank, score
- 최종 선택된 chunk ID
- index/source version
- status와 실행 시각

의료 질문·답변 원문과 폐기된 생성 답변은 Stream, 일반 로그, quarantine, DLQ에도 저장하지 않는다. patient-visible 최종 결과와 Retrieval 실행 메타데이터의 보존 정책을 분리한다.

## Source lifecycle

Source는 owner, license, attribution, checksum, 수집일, 승인자, 승인일, 유효일과 `ACTIVE` 또는 `INACTIVE` 상태를 저장한다. 재색인은 새 `source_version`을 만들고 기존 citation을 바꾸지 않는다. `INACTIVE` source는 신규 retrieval에서 제외하지만 과거 결과의 provenance는 보존한다.

## Track C와 Track F Rule-first 매핑

- Track C Support는 승인된 고정 규칙과 문구만 사용한다. 위기·응급 신호는 안내 문구를 임의 생성하지 않고 승인 경로로 보낸다.
- Track F는 `Context Load → Scope Classifier → Identification Preflight → Rule Engine → Evidence Retrieval → Rerank → Evidence Gate → Answer Composer → Citation Validator → Safety Gate → Persist Result` 순서를 고정한다.
- 자유 ReAct 도구 호출과 열린 웹 검색을 사용하지 않는다. 의미 기반 NLI와 고급 reranking은 Post-MVP-1 완료 게이트에서 제외한다.
- `MEDICATION_GENERAL`과 `OTC_INTERACTION`은 같은 Chat 화면·세션·`CHAT` Job·RAG·Citation·Safety 경로를 사용한다.
- 처방약–OTC 질문은 활성 `interaction_rule`을 결정론적으로 먼저 실행하고 연결된 `rule_evidence`를 Claim Citation으로 반환한다.
- Rule 없음은 안전함이나 함께 복용 가능함을 뜻하지 않는다. OTC 제품·성분·함량·제형을 충분히 식별하지 못하면 Rule 평가를 하지 않고 추가 정보 요청 또는 승인 fallback으로 끝낸다.
- 처방약–처방약 질문에는 Rule 결과를 만들지 않고 범위 제한과 전문가 확인 안내를 반환한다. 음식·음료·보충제 개별 상호작용 판정도 범위 밖이다.
- 근거 없음·상충·Source 비활성·Citation 불일치·Provider 장애·검증 실패에서는 생성 내용을 폐기하고 승인된 제한 응답만 노출한다.

`interaction_rule`은 안정적인 처방약·OTC Identity, rule type·severity, rule/source version, effective 시각과 연결된 `rule_evidence`를 저장한다. 동일 Source 행의 완전 중복만 제거하고 과거 실행은 당시 Rule Set과 Runtime Release Bundle로 재현한다. HIRA 적용약가 데이터는 제품 Identity 또는 상호작용 근거로 사용하지 않는다.

별도 `/api/v1/otc-products`, `/api/v1/otc-evaluations`, `/api/v1/otc-evaluations/{id}`와 `OTC_CHECK` Job, Track D 공개 flag는 두지 않는다. OTC 결과는 기존 Chat 응답의 `release_decision`, `fallback_code`, `citations[]`와 Safety 상태축으로 표현한다.

Runtime Release Bundle은 Source, Candidate/Knowledge Index, Rule, Guideline, Safety, Resolver, Graph, Prompt, Validator, Model과 Worker artifact version을 묶는다. 환경별 Active Bundle은 최대 하나이며 처방·Identification·Bundle·Execution Manifest가 바뀐 과거 결과는 `STALE`이다.

## 보존

사용자에게 보이는 Safety Result와 citation은 계정·사용자 삭제 정책을 따른다. Retrieval 실행 메타데이터는 90일 보존한다. 의료 원문과 질문·답변을 관측용 메타데이터에 복제하지 않는다. 개인정보 또는 의료 검토가 더 엄격한 조건을 정하면 그 조건을 적용한다.

## 공개 게이트

Track C·F는 synthetic fixture로 기술 통합을 검증할 수 있다. 실제 사용자 공개는 승인된 실제형 fixture, 의료·약학·Source·Privacy 검토, HOLDOUT·SAFETY_REGRESSION과 위험 사례 회귀가 모두 통과할 때까지 `PUBLIC_TRACK_C=false`, `PUBLIC_TRACK_F=false`로 차단한다. OTC는 F 게이트를 공유한다. 필수 평가가 `NOT_RUN`이거나 분모가 0이면 `INCONCLUSIVE`로 차단하며 승인 artifact에는 fixture ID, Dataset·Rule·Source·Runtime Bundle version, 분자·분모·95% 신뢰구간, 기대 결과, 검토 범위, 검토자 역할과 승인 시각을 남긴다.
