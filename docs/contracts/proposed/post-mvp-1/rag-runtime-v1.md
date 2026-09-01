# Rule-first Curated Evidence RAG Runtime 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed Target · Not implemented — `proposed/`에서 RAG-00 팀 승인 대기 |
| 구현·리뷰 | Not implemented · Track F Backend·Worker·RAG·Frontend 구현과 지정 리뷰어 검토 대기 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.8` (`PROPOSED_TARGET_NOT_IMPLEMENTED`) |
| Normative Source | `post-mvp-patient-rule-first-curated-evidence-rag-v1.7.md@1.47` · SHA-256 `798dfad94477100d8de242846a3885e6dadf83cc3024b34bcba207d1fdaae932` |
| Physical Target | `rag-detailed-db-schema-v1.md@1.44` · SHA-256 `79d1c6587fab2df1864b9a68d7d5bd23206dd3afded1ad67939cfa31905f3634` |
| Last verified | 2026-08-31 |

## 목적과 적용 범위

사용자가 확정한 현재 처방과 공식 의약품 Identification을 기반으로 Guide·Chat·처방약–OTC 질문을 동일한 Rule-first RAG·Citation·Safety 경로에서 처리한다.

이 문서는 외부 RAG 정본의 Local P0 Runtime 투영본이다. RAG-00 승인, 공유 DTO·DB 계약과 구현·테스트가 완료되기 전에는 현재 Runtime 계약이 아니며 기존 Approved Contract Freeze v4를 자동으로 대체하지 않는다.

- 자유 ReAct Agent, 열린 웹 검색, Graph DB와 승인되지 않은 Source 자동 편입은 사용하지 않는다.
- 고위험·응급·금지 행동 분기는 일반 Retrieval보다 먼저 수행한다.
- OTC는 별도 Job·API·Track이 아니라 기존 `CHAT` Job의 질문 유형이다.
- 처방약–처방약 Rule 판정과 음식·음료·보충제 개별 상호작용 판정은 범위 밖이다.
- 생활습관 출력은 공식 처방약 Identity와 승인 MFDS 근거에 직접 연결된 제한적 음식 주의·일상 활동 Guideline Card로 제한한다.

| 질문·기능 | P0 처리 |
| --- | --- |
| 확정 처방약의 복약법·주의사항·부작용·일반 정보 | 승인 Evidence RAG |
| 처방약–OTC 의약품 상호작용 | 안정 OTC Identity 확인 후 Rule-first·Evidence |
| 처방약 기반 짧은 음식 주의·일상 활동 | 승인 Guideline Card 범위에서만 제공 |
| 처방약–처방약 상호작용 | Rule 판정 제외, 범위 제한·전문가 확인 안내 |
| 음식·음료·보충제 개별 상호작용 | 판정 제외 |
| 진단·처방 변경·용량 조절·응급 자가판단 | 수행 금지, 승인 Safety Flow 또는 전문가 안내 |

## 공유 계약 변경 분류

| 경계 | 분류 | RAG-00 처리 |
| --- | --- | --- |
| Candidate Search·Identification 요청/결과/오류 DTO | 공유 계약 변경 | [Candidate·Identification Target](./medication-candidate-identification-v1.md)과 OpenAPI·Contract Test로 고정 |
| Chat/Guide 접수·상태·결과·오류 DTO | 공유 계약 변경 | Safety 상태축·Citation v2·`no-store`를 함께 고정 |
| OTC | 기존 `CHAT` 질문 유형의 공유 DTO 변경 | 별도 API·Job·Track을 만들지 않음 |
| Citation·Safety 공개 DTO | 공유 계약 변경 | [Safety·Citation v2](./safety-citation-v2.md)로 고정 |
| LangGraph 내부 Node state, Retrieval score·Top-K | 구현 세부 | 공개 DTO에 노출하지 않음 |

## 구현·실행 환경

- Post-MVP-1 RAG Runtime은 Local 환경에서만 구현·실행·검증한다.
- Development·Staging 서버와 환경 승격 자동화는 구축하지 않는다. Git `develop` 브랜치는 협업 통합 브랜치이며 Development 서버를 의미하지 않는다.
- Local PostgreSQL·pgvector·Redis·Worker를 사용하고 환경별 다중 Active Bundle 운영은 이번 구현 범위에서 제외한다.
- Local Runtime의 Active Bundle은 최대 하나다. 향후 서버 환경 추가 시 새 배포·보안·Runtime Bundle Decision을 먼저 승인한다.
- 결정적 Unit·Contract Test는 CI에서 실행할 수 있지만 CI를 Development·Staging Runtime으로 간주하지 않는다.

## 입력과 접수 Preflight

RAG Runtime은 다음 입력만 사용한다.

- 현재 `prescription_version_id`
- 해당 Version의 사용자 확정 Prescription Medication
- 현재 Runtime Release Bundle과 호환되는 append-only `MATCHED` Identification
- 승인된 최소 Patient Context
- Guide 또는 Chat의 허용된 요청 유형

OCR `raw_value`, 정규화 참고값, 검수 전 LLM 초안과 HIRA 데이터는 Runtime 입력이 아니다.

자동 Guide는 모든 활성 Prescription Medication이 현재 Runtime Release Bundle의 활성 공식 제품으로 `MATCHED`일 때만 Job을 생성한다. 미식별·불일치·Source 비활성·Bundle 충돌이면 `job_id` 없는 동기 `REVIEW_REQUIRED`로 끝낸다.

Chat은 약품 식별이 불완전해도 질문과 최소 Chat Job을 먼저 저장하고 Safety Intake·Triage를 실행할 수 있다. `URGENT`, `EMERGENCY`, `UNKNOWN`은 승인 Safety Flow로 즉시 분기하며 일반 Retrieval·Composer·Provider를 호출하지 않는다. `ROUTINE`만 Identification Preflight를 통과한 뒤 일반 Rule·RAG로 진행한다. Preflight 실패는 같은 Job에 승인된 제한 응답을 저장하며 AI Job 안에서 사용자 입력을 기다리지 않는다.

이 Proposed Target이 승인되면 [MFDS 공식 의약품 식별 계약 v1](../../targets/post-mvp-1/medication-identification-v1.md)의 “모든 활성 약제 `MATCHED` 후 Chat Job 생성” 조건은 Chat에 한해 이 2단계 접수 계약으로 대체된다. 자동 Guide의 선행 Identification 조건은 대체하지 않는다. 승인 전에는 두 문서를 임의로 결합하여 현재 Runtime으로 해석하지 않는다.

Job·Outbox·Redis Stream·Worker 상태와 재시도는 [비동기 Job 계약](../../targets/post-mvp-1/async-job-v1.md)과 [Outbox·Stream 계약](../../targets/post-mvp-1/outbox-stream-v1.md)을 따른다. `JOB_EXECUTE` 하나를 소비한 Worker가 아래 Graph를 같은 Job 실행 안에서 처리하며 단계별 두 번째 Outbox를 만들지 않는다.

## 고정 실행 Graph

### Chat Graph

```text
load_chat_intake_context
→ safety_triage
→ scope
→ ROUTINE이면 identification_preflight
→ expand_full_execution_context
→ rule
→ retrieval
→ rerank
→ evidence_gate
→ composer
→ claim_citation_validator
→ release_gate
→ persist
```

### Guide Graph

```text
identification_preflight
→ create_full_execution_context
→ rule
→ retrieval
→ rerank
→ evidence_gate
→ composer
→ claim_citation_validator
→ release_gate
→ persist
```

구현은 LangGraph의 명시적 Node와 조건부 Edge를 사용한다. Chat과 Guide의 접수 경계는 다르지만 Full Execution Context가 생성된 이후의 fail-closed Rule·Evidence·Citation·Release 경계는 공유한다. `release_gate`는 최종 환자 공개 판정이며 Safety Triage와 같은 단계가 아니다.

### Rule-first

- 처방약–OTC 질문은 활성 `interaction_rule`을 결정론적으로 먼저 실행한다.
- 양성 Rule은 연결된 `rule_evidence`를 Claim Citation으로 사용한다.
- Rule 없음은 안전, 복용 가능 또는 상호작용 없음을 뜻하지 않는다.
- OTC 제품·성분·함량·제형을 안정적으로 식별하지 못하면 Rule 평가를 실행하지 않고 추가 정보 요청 또는 승인 fallback으로 끝낸다.
- OTC 자유 입력 Identity 전이는 아직 승인되지 않았으므로 LLM 추론만으로 OTC Identity를 확정하지 않는다.

### Retrieval·Rerank·Evidence Gate

- 검색 대상은 승인·활성 Source Snapshot의 Knowledge Chunk와 Rule Evidence다.
- `pg_trgm`·Dense 검색과 rerank 구현은 versioned configuration으로 재현한다.
- 내부 Top-K·score는 공개 DTO에 노출하지 않는다.
- 의료 Claim과 처방약 기반 Guideline Claim은 승인된 Source version과 locator를 가져야 한다.
- 근거 없음·상충·Source 비활성·만료·Citation 불일치에서는 생성 내용을 폐기하고 승인 fallback만 저장한다.

고급 의미 기반 NLI와 고급 reranking은 Post-MVP-1 완료 Gate 범위 밖이다. 구현 PR은 도입하지 않은 기능을 평가 완료로 표시하지 않는다.

## Intake Context와 Full Execution Context

Chat은 접수 시점과 일반 RAG 실행 시점의 Snapshot을 분리한다.

| 구분 | 생성 Transaction | 고정 범위 | 사용 분기 |
| --- | --- | --- | --- |
| Chat Intake Context | Chat Message·최소 AI Job·`ai_job_intake_context`·단일 `JOB_EXECUTE` Outbox를 함께 저장 | 요청 사용자·현재 Prescription Version 참조, 최소 Safety Patient Context, 질문 digest·scope, Safety Policy·Runtime Bundle/환경 참조 | 모든 Chat Safety Triage |
| Full Execution Context | `ROUTINE` Safety 판정 후 Identification Preflight가 현재 처방·Identification·Bundle을 잠금 재검증하고 `ai_job_execution_context`와 Identification member를 원자적으로 확장 | Prescription Version 전체, 최소 Patient Context Snapshot, 현재 `MATCHED` Identification 목록, Source·Index·Rule·Prompt·Validator·Model·Execution Manifest version | 일반 Rule·Retrieval·Composer |
| Guide Full Context | Job 생성 전 Identification Preflight 후 Guide Job·Full Context·Identification member·단일 Outbox를 함께 저장 | 위 Full Execution Context와 동일 | 자동 Guide 전체 경로 |

- Chat 접수 Transaction에서는 Full Execution Context를 미리 만들지 않는다. `URGENT`, `EMERGENCY`, `UNKNOWN`은 Full Context 없이 승인 Safety 결과로 종료할 수 있다.
- Chat `ROUTINE` 확장 시 Intake의 Prescription Version·환경·Bundle 참조와 잠금 재검증한 현재값이 다르면 Full Context를 만들지 않고 `AI_JOB=STALE`, `release_decision=STALE`로 종료하며 일반 RAG를 실행하지 않는다.
- Full Context 생성과 Identification member 저장은 하나의 Transaction이다. 부분 Snapshot은 허용하지 않는다.
- Worker 재시도는 이미 고정된 Intake/Full Context만 읽고 현재 상태를 다시 선택하지 않는다. Full Context가 없는 `ROUTINE` 재시도는 같은 원자적 확장 절차를 다시 수행한다.
- Worker의 Preflight·결과 commit Transaction은 [처방 버전 계약의 전역 잠금 순서](../../targets/post-mvp-1/prescription-version-v1.md#동시-수정)인 `PRESCRIPTION → CHAT_SESSION(해당 시) → AI_JOB → 도메인 row → OUTBOX(해당 시)`를 따른다.
- 결과 commit 직전에도 위 순서로 고정 Context와 현재 Prescription·Patient Context·Identification·Bundle·Execution Manifest·runtime revision을 재검증한다. 불일치 시 생성 결과를 공개하지 않고 `AI_JOB=STALE`, `release_decision=STALE`, `is_current=false`로 저장한다.

## Runtime Release Bundle과 실행 Snapshot

Runtime Release Bundle은 다음 version을 함께 고정한다.

- Source Snapshot과 Candidate·Knowledge Index
- Rule·Guideline·Safety policy
- Resolver·Graph·Prompt·Validator·Model
- Execution Manifest와 Worker artifact

Guide Job 접수 Transaction은 Full Execution Context 전체를 고정한다. Chat Job 접수 Transaction은 Intake Context만 고정하고, `ROUTINE` 분기에서 Identification Preflight 후 Full Execution Context를 원자적으로 확장한다. Worker 재시도는 각 단계에서 이미 고정된 Snapshot만 읽는다.

처방·Patient Context·Identification·Bundle·Execution Manifest·runtime revision이 달라지면 이전 결과는 `AI_JOB=STALE`, `release_decision=STALE`, `is_current=false`이며 현재 답변으로 공개하지 않는다.

`RETRY_WAIT` 중 Active Bundle 변경과 구·신 Worker 동시 실행의 호환성·drain 방식은 아직 미정이다. [문서 권위의 후속 Product Decision 항목](../../../governance/post-mvp-1-document-authority.md#구현-전-재결정이-필요한-충돌)이 승인되기 전에는 Runtime Bundle을 Current로 승격하지 않는다.

## 결과·Citation·상태

상태축, 허용 조합, fallback과 공개 DTO의 RAG 목표는 [Safety·Citation v2 Proposed Target](./safety-citation-v2.md)을 따른다. 기존 [Safety Result v1](../../targets/post-mvp-1/safety-result-v1.md)은 선행 Approved v4 Target이며 v2 구현 PR이 DTO·OpenAPI·Migration·Contract Test와 함께 승인되기 전까지 현재 Runtime을 변경하지 않는다.

Citation 목표 유형은 `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `INTERACTION_RULE`, `LIFESTYLE_GUIDELINE`, `SAFETY_POLICY`다. 각 의료 Claim은 유형별 Evidence FK 하나와 승인된 Source version·locator를 가져야 한다. 공개 Citation은 `citation_id`, `claim_key`, `source_type`, `title`, nullable `url`, `source_version`, `locator`, 짧은 `excerpt`만 포함한다.

Retrieval Run에는 raw 질문을 저장하지 않고 versioned query HMAC/digest, filter Snapshot, `prescription_version_id`, 질문 유형, 검색 Chunk ID·rank·score, 최종 선택 Chunk, Index·Source version, 상태와 실행 시각만 저장한다.

Candidate Search·Identification·OTC 질문·Chat/Guide 접수·Job 상태·결과·Citation과 그 오류 응답은 [Candidate·Identification 계약의 소유권 경로](./medication-candidate-identification-v1.md#소유권transaction현재성)에 따라 동일한 `user_id` 소유권을 검증하고 `Cache-Control: no-store`를 포함한다. 존재하지 않거나 타 사용자 리소스는 부작용 없이 `404`로 통일한다. 이 헤더는 OpenAPI 설명과 Contract Test에서 고정한다.

## 평가 후보 Guard Operation

보호된 Local Evaluation Runner는 Run 시작 시 `operation_type=EVALUATION_CANDIDATE` Guard로 정확한 `BUILDING` 후보 Bundle ID·Manifest Hash와 전체 Source·Snapshot·승인·Freshness·Scope Policy·Revocation 상태를 검사한다. 각 Required Case는 별도 `EVALUATION_REQUEST` Guard로 실제 Selection과 요청 Scope를 환자용 `REQUEST`와 같은 규칙으로 검사하고 같은 Run의 Candidate Guard에 결속한다.

두 Guard는 합성·비식별 Dataset과 승인된 Runner만 허용한다. 평가 답변은 환자 API·Application 결과 테이블에 저장하거나 공개하지 않고 Evaluation Artifact에만 기록한다. 모든 Required Case Guard와 동일 Manifest 평가가 통과하고 독립 승인이 기록되기 전에는 Bundle을 `READY` 또는 Active로 전환하지 않는다.

## 최소 검증

- 미확정 OCR/LLM 값과 HIRA 입력 차단
- 자동 Guide의 모든 활성 약 `MATCHED` 전 Job 미생성과 동기 `REVIEW_REQUIRED`
- Chat 최소 Job·Safety Triage 선행, `ROUTINE`만 Identification Preflight 이후 일반 RAG 실행
- Chat Intake Context와 Full Execution Context 분리, `ROUTINE` 원자적 확장 실패의 `STALE`와 부분 Snapshot 0건
- Guide Preflight 후 Job·Full Context·Identification member·Outbox 원자적 생성
- 단일 `JOB_EXECUTE`에서 고정 Graph 실행과 두 번째 단계 Outbox 미생성
- Rule 양성·Rule 없음·OTC Identity 불충분의 fail-closed 분기
- 승인 Source만 Retrieval하고 비활성·만료·상충 Source 차단
- Claim–Citation 완전성, locator 유효성, Citation 변조 차단
- Prompt Injection과 검색 근거 밖 의료 주장 차단
- 처방·Context·Identification·Bundle·Execution Manifest 변경의 `STALE`
- 안전한 `REJECTED` fallback commit과 실제 실행 `FAILED` 구분
- 타 사용자·이전 Prescription Version 결과 공개 차단
- raw 질문·Source 원문·폐기 답변의 Stream·로그·quarantine·DLQ 미저장
- 모든 환자용 응답의 `Cache-Control: no-store`
- Run `EVALUATION_CANDIDATE`와 Case `EVALUATION_REQUEST` 결속, 환자 API·Application 결과 공개 0건

## 공개 게이트

구현·계약·통합·RAG 평가와 `EXT-MED-002`, `EXT-PHARM-001`, `EXT-SOURCE-001`, `EXT-SOURCE-002`, `EXT-PRIV-001`, `EXT-PRIV-002`, `EXT-SAFETY-001`이 완료될 때까지 `PUBLIC_TRACK_F=false`를 유지한다. Development·Staging 서버를 만들지 않으며 실제 사용자 공개는 이번 Local 구현 범위가 아니다.
