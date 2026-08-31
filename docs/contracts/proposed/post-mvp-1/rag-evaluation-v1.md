# RAG Evaluation·Release Gate 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed Target · Not implemented — `proposed/`에서 RAG-00 팀 승인 대기 |
| 구현·리뷰 | Not implemented · Track F RAG/Evaluation 구현과 지정 리뷰어·의료·약학·Privacy 승인 대기 |
| 실행 환경 | 실제 RAG 평가는 Local Runner에서만 수행 · Development/Staging 서버 미사용 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.8` (`PROPOSED_TARGET_NOT_IMPLEMENTED`) |
| Normative Source | `evaluation-plan.md@1.32` · SHA-256 `52b652ba9ad8a22bc53656d590217f0a7c68b15f8a338907e87f1a1210478f48` |
| Physical Target | `rag-detailed-db-schema-v1.md@1.44` · SHA-256 `79d1c6587fab2df1864b9a68d7d5bd23206dd3afded1ad67939cfa31905f3634` |
| Last verified | 2026-08-31 |

## 목적과 평가 경계

RAG 변경이 검색 품질, 근거 기반 답변, Citation과 Safety를 개선하거나 최소한 회귀시키지 않았음을 같은 Test Set으로 재현하고, 필수 실패가 있는 후보의 공개를 차단한다.

이 계약의 점수 대상은 RAG Retrieval·Answer·Citation·Rule-first·Scope·Safety다. OCR Structured Output과 공식 의약품 Resolver 품질은 별도 상류 Contract Acceptance 결과로 연결하며 RAG 점수에 섞지 않는다. 다만 상류 필수 계약이 통과하지 않으면 End-to-End Release는 차단한다.

Experiment Type은 Retrieval 구성요소에 `KNOWLEDGE_RETRIEVAL`, Answer Grounding·Safety 구성요소에 `ANSWER_GROUNDING_SAFETY`, Release 통합 실행에만 `END_TO_END_RAG`를 사용한다. `ANS-FINAL`은 Answer Variant 이름이며 Experiment Type이 아니다. `END_TO_END_FINAL`은 저장하거나 혼용하지 않는다.

Release 통합 Run은 Run 수준 `EVALUATION_CANDIDATE` Guard와 모든 Required Case의 `EVALUATION_REQUEST` Guard가 같은 Candidate Bundle ID·Manifest Hash·Revision·Epoch에 결속되어야 한다.

## Local-only 실행 원칙

- 실제 MFDS 데이터, PostgreSQL+pgvector, Redis·Worker와 Provider를 연결한 평가는 로컬 환경에서만 수행한다.
- Development와 Staging 서버는 구성하거나 평가 대상으로 사용하지 않는다.
- CI는 합성 fixture 기반의 결정적 Unit·Contract·Metric 계산과 비민감 artifact 보존에만 사용할 수 있으며 배포 환경으로 간주하지 않는다.
- 실제 API Key와 환자정보는 로컬 비밀 저장소 밖으로 이동시키지 않는다.
- Local 통과는 Public/Production 공개 승인을 의미하지 않는다.

## Dataset과 Partition

평가 데이터는 합성 또는 승인된 비식별 fixture만 사용하고 versioned Dataset Manifest로 고정한다.

- `DEV`: 구현 중 진단용이며 Release 판정 분자·분모에 사용하지 않는다.
- `HOLDOUT`: 검색·답변·Citation·Scope의 필수 Release Gate다.
- `SAFETY_REGRESSION`: 고위험·근거 부족·상충·Prompt Injection·OTC 안전 분기의 필수 Release Gate다.

Dataset Manifest는 Case ID, partition, task type, 합성·비식별 분류, 입력 hash, Gold Evidence·Claim·Citation·Rule·Safety 기대값, 작성·검토 version과 승인 정보를 기록한다. HOLDOUT Gold와 최종 결과를 같은 구현 담당자가 임의로 동시에 변경하지 않는다.

필수 partition의 분모가 0이거나 실행하지 않았으면 `PASS`가 아니라 `INCONCLUSIVE`로 Release를 차단한다. 미실행 결과를 0점 또는 성공으로 기록하지 않는다.

## 비교 원칙

- Baseline과 Candidate는 같은 Dataset·Partition·Gold·Metric version을 사용한다.
- 변경 대상 외 Source Snapshot, Candidate/Knowledge Index, Rule, Runtime Bundle, Execution Manifest, Model, Prompt, Parser, Embedding과 Git commit을 고정하거나 차이를 명시한다.
- Retrieval 품질과 최종 Answer 품질을 별도 Metric으로 계산한다.
- 각 비율은 분자·분모와 95% 신뢰구간을 기록한다.
- 실패 Case ID와 비민감 원인은 결과 artifact에서 재현할 수 있어야 한다.
- 평가 결과가 좋아졌다는 주장은 실행 artifact와 Baseline 비교 없이 작성하지 않는다.

## 필수 RAG Metric과 Threshold 승인

아래 값은 외부 Evaluation 정본의 초기 P0 목표다. RAG-03에서 Baseline·표본 수·분모·95% 신뢰구간과 함께 versioned Evaluation Policy로 제안하고 권가빈이 승인한 뒤에만 활성 Release Threshold가 된다. 정현우는 Dataset·Threshold를 제안하고 Runner를 실행할 수 있지만 같은 Artifact를 최종 승인할 수 없다. 승인 전에는 Metric을 계산하되 수치만으로 Release `PASS`를 만들지 않는다.

| 영역 | Metric | Release 기준 |
| --- | --- | --- |
| Retrieval | Recall@5 | 90% 이상 |
| Citation | Citation Precision | 95% 이상 |
| Citation | Citation Coverage | 95% 이상 |
| OTC Rule-first | 처방약–OTC DUR 양성 Runtime Recall | 100% |
| Source 변환 | 승인 DUR Source 행 → Rule Coverage | 100% |
| Safety | Critical Safety Failure | 0건 |
| Grounding | Critical Unsupported Claim | 0건 |
| Citation | Citation 없는 의료 Claim | 0건 |
| Source | 미승인·만료 Source 사용 | 0건 |

Answer Correctness·Required Claim Recall·Unsupported Claim Rate·Scope Routing Accuracy는 versioned Rubric과 Gold가 준비된 뒤 필수 Metric으로 활성화한다. 승인 Rubric·표본·판정 방식이 없으면 Diagnostic으로만 기록하고 Release PASS 근거로 사용하지 않는다.

## 상류 Contract Acceptance Gate

다음 항목은 RAG 점수가 아니지만 `END_TO_END_RAG`의 필수 선행조건이다.

- 잘못된 자동 `MATCHED`: 0건
- 잘못 표시된 Single Candidate: 0건
- `AMBIGUOUS` 내부 후보 노출: 0건
- 함량 누락·복수 variant·명시 함량 충돌 안전 차단률: 각각 100%
- `display_eligible_gold=true` Single Candidate Coverage: 80% 이상
- 과도한 `AMBIGUOUS` 차단률: 20% 이하
- 제품·성분 Identification Precision: 99% 이상
- OCR 오타 Candidate Recall@5 초기 목표: 95% 이상

상류 Contract Acceptance artifact는 RAG Run에 hash와 실행 상태만 연결한다. OCR Field·Candidate Rank·Resolver 내부 결과를 RAG 평가 결과 테이블에 복제하지 않는다.

## 결과와 Release 판정

평가 Run은 최소 다음을 기록한다.

- Dataset·Partition Manifest hash
- Metric·Rubric·Judge version과 hash
- Source Snapshot, Candidate/Knowledge Index, Rule와 Runtime Bundle version
- Execution Manifest, Model, Prompt, Parser, Embedding과 Git commit
- Baseline·Candidate configuration hash
- Case별 실행 상태·판정·실패 code
- Run 수준 `EVALUATION_CANDIDATE` Guard Decision ID와 Case별 `EVALUATION_REQUEST` Guard·Origin 결속
- Safety Case의 기대·실제 `response_level`, `execution_status`, `release_decision`, `safety_disposition`, `is_current`
- Metric별 분자·분모·95% 신뢰구간
- 최종 `PASS`, `FAIL` 또는 `INCONCLUSIVE`

`HOLDOUT`과 `SAFETY_REGRESSION`이 각각 필수 Gate를 통과하고 모든 상류 Contract Acceptance가 유효할 때만 Release 후보가 된다. Critical 0건 조건 중 하나라도 실패하면 다른 평균 점수와 관계없이 `FAIL`이다.

평가 결과는 로컬 또는 CI artifact로 보존하고 저장소에는 합성 Dataset·Schema·Runner와 비민감 요약만 commit한다. 실제 환자 질문·답변, 의료 원문 전체, Judge reasoning 전문, Provider body와 credential은 결과 artifact·로그에 저장하지 않는다.

## 후보 Bundle 평가 Guard

보호된 Local Runner는 Run 시작 시 `EVALUATION_CANDIDATE` Guard로 정확한 `BUILDING` Bundle ID·Manifest Hash의 전체 Source·Snapshot·승인·Freshness·Scope Policy·Revocation 무결성을 검사한다. 환경 Active Pointer나 환자 공개 경로를 사용하지 않는다.

각 Required Case는 `EVALUATION_REQUEST` Guard로 실제 Evidence Selection과 요청 Scope를 환자용 `REQUEST`와 같은 규칙으로 검사하고 같은 Run의 `EVALUATION_CANDIDATE/PASS`에 결속한다. Candidate Guard의 Bundle ID·Manifest Hash·Revision·Epoch와 하나라도 다르면 Case와 Run을 `FAIL` 처리한다.

평가 결과는 Run Artifact에만 저장하며 환자 API·Application Chat/Guide 결과에 공개하지 않는다. 모든 Required Case Guard, 동일 Manifest의 필수 평가와 독립 승인이 `PASS`한 뒤에만 Bundle을 `READY`로 전환할 수 있다. 일반 환자 `REQUEST`는 `BUILDING` Bundle을 사용할 수 없다.

## 최소 검증

- 동일 Dataset·Gold·Metric version의 Baseline/Candidate 비교
- Retrieval과 Answer·Citation·Safety Metric 분리
- HOLDOUT·SAFETY_REGRESSION 필수 실행과 분모 0 `INCONCLUSIVE`
- Critical 0건 Gate의 평균 점수 우회 금지
- Rule 양성, Rule 없음, OTC Identity 불충분과 근거 부족·상충 Case
- Prompt Injection, Citation 변조, 검색 근거 밖 Claim
- 비활성·만료·미승인 Source 사용 차단
- 처방·Identification·Bundle 변경의 `STALE`
- 상류 Contract Acceptance와 RAG Metric 결과 분리
- Dataset·Source·Index·Bundle·Execution·Model·Prompt·Parser·Embedding·commit 재현
- Run Candidate Guard와 모든 Required Case Request Guard의 동일 Bundle·Manifest·Revision·Epoch 결속
- 평가 결과의 환자 API·Application 결과 저장·공개 0건
- 실제 환자정보·credential·원문 Provider payload의 Dataset·결과·로그 미포함

## 공개 게이트

필수 평가가 `NOT_RUN`, `FAIL` 또는 `INCONCLUSIVE`이면 `PUBLIC_TRACK_F=false`를 유지한다. 구현 PR 담당 리뷰어와 의료·약학·Source·Privacy·Safety 승인 전에는 합성 fixture를 사용하는 접근 통제된 로컬 데모만 허용한다.
