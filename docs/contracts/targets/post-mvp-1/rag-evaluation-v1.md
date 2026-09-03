# RAG Evaluation·Release Gate 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target · Not implemented — RAG-00 / 2026-09-01 |
| 구현·리뷰 | Evaluation Schema Set 1.2 implemented candidate · 지정 책임 리뷰와 나머지 Track F 구현·외부 승인 대기 |
| 실행 환경 | 실제 RAG 평가는 Local Runner에서만 수행 · Development/Staging 서버 미사용 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`; 저장소 투영 상태는 `Approved Target · Not implemented` |
| Normative Source | `evaluation-plan.md@1.35` · SHA-256 `526f83dedc05a777c0963bfa10bb8bd8ebd940ab3eb12523f4c8fa15447e542f` |
| Physical Target | `rag-detailed-db-schema-v1.md@1.47` · SHA-256 `f88ec11aaa6671184f2d0f5076219bf2ad51525b9e6a136ec5389afd2af82aea` |
| Last verified | 2026-09-02 |

## 목적과 평가 경계

RAG 변경이 검색 품질, 근거 기반 답변, Citation과 Safety를 개선하거나 최소한 회귀시키지 않았음을 같은 Test Set으로 재현하고, 필수 실패가 있는 후보의 공개를 차단한다.

이 계약의 점수 대상은 RAG Retrieval·Answer·Citation·Rule-first·Scope·Safety다. OCR Structured Output과 공식 의약품 Resolver 품질은 별도 상류 Contract Receipt로 연결하며 RAG 점수에 섞지 않는다. 다만 상류 필수 Receipt가 통과하지 않으면 End-to-End Release는 차단한다.

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

- `AUTHORING`: Rule·Prompt·Test Case 작성 참고용이며 점수·Release 판정 분자·분모에 사용하지 않는다.
- `DEV`: 구현 중 진단용이며 Release 판정 분자·분모에 사용하지 않는다.
- `HOLDOUT`: 검색·답변·Citation·Scope의 필수 Release Gate다.
- `SAFETY_REGRESSION`: 고위험·근거 부족·상충·Prompt Injection·OTC 안전 분기의 필수 Release Gate다.

Dataset Manifest는 Case ID, partition, task type, 합성·비식별 분류, 입력 hash, Gold Evidence·Claim·Citation·Rule·Safety 기대값, 작성·검토 version과 승인 정보를 기록한다. HOLDOUT Gold와 최종 결과를 같은 구현 담당자가 임의로 동시에 변경하지 않는다.

`question_template`, `source_segment`, `medication_family`, `transform_origin`을 최소 Leakage Group 축으로 저장한다. 같은 Group 축을 공유하는 원본과 파생 Case를 서로 다른 Partition에 배치하거나 독립 표본 여러 건으로 계산하지 않는다. HOLDOUT 실행 전 Comparison Policy에 Metric별 분석 단위, `cluster_dimension`, 최소 Case 수, 최소 독립 Group 수와 95% 신뢰구간 계산 방식을 고정한다.

필수 Partition을 실행하지 않았으면 `execution_status=NOT_EVALUATED`, `decision_status=null`로 Release를 차단한다. 실행을 완료했지만 분모가 0이거나 최소 Case·독립 Group 수가 부족할 때만 `COMPLETED/INCONCLUSIVE`로 기록한다. 미실행 결과를 0점, `FAIL`, `INCONCLUSIVE` 또는 성공으로 위장하지 않는다.

### Evaluation Schema Set 1.1

`#214` Dataset Freeze 입력 후보는 `rag-eval.schema-set@1.1.0`, SHA-256 `5cfb113e45a4c333fef05830b0d7c2401975ce66b53dc68ff054b08ba79822c0`이다. 18개 전체 member 중 `rag-eval.case`와 `rag-eval.dataset-manifest`만 `1.1.0`이며 나머지 16개 member는 기존 `1.0.0` canonical 계약을 byte-for-byte 재사용한다. 지정 책임 리뷰 승인 전에는 이 후보를 승인 완료된 Freeze 입력으로 해석하지 않는다.

Safety·End-to-End Gold는 Rule 결과를 `MATCHED_RULES | NO_MATCH | NOT_INVOKED`로 구분한다. `MATCHED_RULES`만 비어 있지 않은 `expected_rule_ids`를 가지며, `NO_MATCH`와 `NOT_INVOKED`는 빈 배열을 사용한다. `MATCHED_RULES | NO_MATCH`는 Source·Bundle이 모두 적격이어야 한다. `NOT_INVOKED`는 `SAFETY_ROUTED | SOURCE_INELIGIBLE | BUNDLE_INELIGIBLE` 중 하나의 typed reason, `dependency_fault=NONE`, Provider·Retrieval 미호출을 요구한다. `SAFETY_ROUTED`는 Source·Bundle이 모두 적격일 때만 허용하며 Source 비적격은 `SOURCE_INELIGIBLE`, Scope·Member 비적격은 `BUNDLE_INELIGIBLE`로 기록한다. Provider·Retrieval fault는 Rule-first 이후의 실패이므로 이미 확정된 `MATCHED_RULES | NO_MATCH` 결과와만 결속한다. Answer-only Case는 Rule outcome을 소유하지 않으므로 Rule ID도 `null`이다.

`expected_scope_codes`는 매칭된 Rule의 부속값이 아니라 해당 Evaluation Request가 Guard에 제출하는 기대 요청 Scope다. 따라서 Rule 결과와 독립적으로 Safety·End-to-End Case에서 비어 있지 않아야 하며, Case 기대 Scope와 EVALUATION_REQUEST의 정렬 Scope·Scope Manifest Hash를 exact-match한다. Source eligibility, Bundle eligibility와 Provider/Retrieval dependency fault도 typed fixture로 기록하고 free-form tag나 가짜 Rule ID를 사용하지 않는다.

Loader는 Case·Dataset Manifest의 payload version을 Schema Set version 자체가 아니라 선택된 registry의 해당 member version과 비교한다. 따라서 후속 Schema Set이 바뀌지 않은 authoring member를 재사용해도 Set version을 member version으로 오인하지 않는다.

Schema `1.1.0`의 `FROZEN` Dataset은 모든 Case Gold, Evidence Mapping과 Critical Claim Rubric의 Team `APPROVED` closure가 완전해야 Loader를 통과한다. 하나라도 `DRAFT | REVIEWED`이면 `EVAL_REVIEW_PROVENANCE_INVALID`로 실패한다. 상세 결정은 [RAG Evaluation Schema Set 1.1 Freeze](../../../governance/decisions/2026-09-02-rag-evaluation-schema-set-1-1-freeze.md)를 따른다.

### Evaluation Schema Set 1.2

`#214` Dataset 후보의 실제 검토 provenance는 `rag-eval.schema-set@1.2.0`, SHA-256 `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06`을 사용한다. 18개 member 중 Case, Dataset Manifest, Evidence Mapping, Critical Claim Rubric, Evaluation Profile, Suite Definition, Evaluation Policy, Protected Artifact Receipt 8개만 member `1.2.0`이며 나머지 10개 member는 기존 canonical bytes와 member version을 재사용한다.

`ReviewProvenance@1.2`는 상태와 event를 양방향으로 맞춘다. `DRAFT`는 `reviewed_by=null`, `reviewed_at=null`, 승인 필드 `null`, `evidence_review_refs=[]`만 허용한다. `REVIEWED`는 reviewer·review timestamp와 immutable review evidence를 하나 이상 요구하고 승인 필드는 `null`이다. `APPROVED`는 이 review event에 더해 approver·approval timestamp를 요구한다. reviewer와 approver의 한쪽 필드만 기록하는 payload는 허용하지 않는다.

`REVIEWED`·`APPROVED` 상태의 `reviewed_by.role`은 `EVALUATION_REVIEWER`만 허용한다. 이 역할은 Case Gold·Fixture·Evidence 등 팀 내부 Evaluation 검토를 뜻하며, `MEDICAL_REVIEWER` 또는 외부 의료·약학 approval을 뜻하지 않는다. `external_medical_review_status`와 immutable external receipt 규칙을 대체하지 않는다. Safety Case와 Dataset Manifest의 Team approval 역할 제한은 기존대로 각각 `PRODUCT_SAFETY_REVIEWER | MEDICAL_REVIEWER`, `DATASET_CUSTODIAN`을 유지한다.

Exported Draft 2020-12 JSON Schema는 field type·requiredness·enum·state conditional 등 구조 제약의 portable preflight다. 작성자·검토자·승인자의 cross-field identity 중복, system actor, actor role 조합과 event timestamp 순서는 표준 JSON Schema만으로 portable하게 비교할 수 없으므로 Loader가 Pydantic `ReviewProvenanceV12` 검증으로 fail-closed한다. JSON Schema 단독 통과는 Dataset 수용이나 Freeze 자격을 뜻하지 않으며, Dataset graph는 반드시 Loader로 검증한다.

Loader는 manifest가 선택한 1.2 bundle로 Case뿐 아니라 Evidence Mapping, Rubric, Profile, Evaluation Policy, Suite, Protected Artifact Receipt까지 검증하고, graph의 모든 schema payload version을 registry member version과 exact-match한다. 기존 1.0/1.1의 validation·canonical bytes는 불변이다. 상세 결정은 [RAG Evaluation Schema Set 1.2 Freeze](../../../governance/decisions/2026-09-03-rag-evaluation-schema-set-1-2-freeze.md)를 따른다.

## 비교 원칙

- Baseline과 Candidate는 같은 Dataset·Partition·Gold·Metric version을 사용한다.
- 변경 대상 외 Source Snapshot, Candidate/Knowledge Index, Rule, Runtime Bundle, Execution Manifest, Model, Prompt, Parser, Embedding과 Git commit을 고정하거나 차이를 명시한다.
- Retrieval 품질과 최종 Answer 품질을 별도 Metric으로 계산한다.
- 각 비율은 분자·분모와 95% 신뢰구간을 기록한다.
- 실패 Case ID와 비민감 원인은 결과 artifact에서 재현할 수 있어야 한다.
- 평가 결과가 좋아졌다는 주장은 실행 artifact와 Baseline 비교 없이 작성하지 않는다.

## RAG Metric과 Threshold 승인

아래 값은 외부 Evaluation 정본의 비정본 P0 목표 예시다. RAG-03에서 Baseline·표본 수·분모·독립 Group 수·95% 신뢰구간과 함께 versioned Comparison/Evaluation Policy로 제안하고 권가빈이 승인한 뒤에만 활성 Release Threshold가 된다. 정현우는 Dataset·Threshold를 제안하고 Runner를 실행할 수 있지만 같은 Artifact를 최종 승인할 수 없다. 승인 전에는 Metric을 계산하되 예시 수치만으로 Release `PASS`를 만들지 않는다.

| 영역 | Metric | 초기 목표·판정 방향 |
| --- | --- | --- |
| Retrieval | Recall@5 | 90% 이상 |
| Retrieval | MRR | Baseline 대비 비퇴행 |
| Retrieval | nDCG@5 | Baseline 대비 개선 |
| Retrieval | No-hit Rate | Slice별 기록·Baseline 대비 감소 |
| Citation | Citation Precision | 95% 이상 |
| Citation | Citation Coverage | 95% 이상 |
| Citation | Citation Entailment | 승인 Rubric Threshold Freeze 후 필수화 |
| OTC Rule-first | 처방약–OTC DUR 양성 Runtime Recall | 100% |
| Safety | Critical Safety Failure | 0건 |
| Safety | 고위험 Safety Routing Accuracy | 100% |
| Grounding | Critical Unsupported Claim | 0건 |
| Citation | Citation 없는 의료 Claim | 0건 |
| Runtime Governance | 부적격 Bundle 부분 실행·Fallback 없는 공개 | 0건 |
| Scope | 처방약–처방약에 `SAFE`·`NO_INTERACTION` 표현 | 0건 |
| Scope | 음식·음료·보충제 개별 상호작용 판정 | 0건 |
| Lifestyle | 승인 근거 없는 식이·활동 행동 제안 | 0건 |
| Privacy | 보험코드·OCR 원문·내부 Identifier Digest 노출 | 0건 |

Answer Correctness·Required Claim Recall/Completeness·Unsupported Claim Rate·질문 Relevance·Scope Routing Accuracy는 versioned Rubric과 Gold가 준비된 뒤 필수 Metric으로 활성화한다. 승인 Rubric·표본·판정 방식이 없으면 Diagnostic으로만 기록하고 Release PASS 근거로 사용하지 않는다. 승인 DUR Source 행 → Rule 변환 Coverage, Source·DB Governance와 OCR·Resolver 검증은 통계 RAG Metric이 아니라 별도 Contract Receipt가 소유한다.

## 상류 Contract Receipt Gate

OCR·Resolver 품질 비교와 Candidate 검색 알고리즘별 성능은 RAG 정량 평가에서 제외한다. 다음 입력 경계는 별도 Unit·Contract·Integration Suite가 검증하며 `END_TO_END_RAG` Run에는 Suite Version·Commit·Migration·Fixture/Input Manifest·Artifact ID/Hash와 실행·판정 상태를 가진 불변 Contract Receipt만 연결한다.

- RAG 입력은 사용자 확인이 끝난 활성 Prescription Version과 현재 Bundle의 `MATCHED` Medication Identity만 사용한다.
- 미확인·`AMBIGUOUS`·`UNRESOLVED`·만료·비활성 제품이면 일반 RAG Execution을 만들지 않는다. Chat 최소 Safety Intake만 예외적으로 허용한다.
- 보험코드 원문·Digest·내부 매칭 상태는 RAG Prompt·답변·Citation·공개 UI에 전달하지 않는다.
- OCR `raw_value`, `normalized_value`, 검수 전 Structured Output은 Candidate·Rule·RAG 의료 근거로 사용하지 않는다.
- Candidate Finalizer·단일 후보·함량 충돌·사용자 확인·append-only Identification 불변식은 Candidate/OCR Contract Suite가 소유한다.
- 약제별 활성 `RUNNING | READY` Candidate Search 최대 1개, 동일 Context Search 재사용, 상이 Context Search 무효화와 서로 다른 Search ID 동시 확인 단일 성공은 Candidate Contract Suite가 소유한다. 이 결과는 RAG 품질 Metric에 합산하지 않는다.

상류 Contract Receipt가 `COMPLETED/PASS`가 아니면 RAG 품질 결과가 PASS여도 Release를 차단한다. 미구현·미실행·오류 Receipt는 품질 `FAIL`로 위장하지 않고 각 `execution_status`와 `decision_status=null`을 유지한다. OCR Field·Candidate Rank·Resolver 내부 결과와 상류 품질 Metric은 RAG Dataset·Case Result·Metric 테이블에 복제하지 않는다.

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
- 각 Run·Metric·Suite·Case의 `execution_status`와 nullable `decision_status`

`HOLDOUT`과 `SAFETY_REGRESSION`이 각각 필수 Gate를 통과하고 모든 상류 Contract Receipt가 유효할 때만 Release 후보가 된다. Critical 0건 조건 중 하나라도 실패하면 다른 평균 점수와 관계없이 `FAIL`이다.

### 실행 상태와 판정 상태

| 상황 | `execution_status` | `decision_status` | Release 처리 |
| --- | --- | --- | --- |
| Runner 또는 필수 Metric 미구현 | `NOT_IMPLEMENTED` | `null` | 차단 |
| 실행 전 또는 필수 Contract Receipt 미검증 | `NOT_EVALUATED` | `null` | 차단 |
| Schema·Hash·Policy·통제 변수 불일치 | `INVALID` | `null` | 차단 |
| Runner·Provider·Judge·Contract 실행 오류 | `ERROR` | `null` | 차단 |
| 실행 완료·기준 통과 | `COMPLETED` | `PASS` | 다른 필수 항목도 모두 PASS일 때만 후보 |
| 실행 완료·기준 실패 | `COMPLETED` | `FAIL` | 차단 |
| 실행 완료·분모 0·표본 또는 독립 Group 부족 | `COMPLETED` | `INCONCLUSIVE` | 차단 |
| 승인 Policy상 비필수 Diagnostic | `COMPLETED` | `N/A` | Release 근거 사용 금지 |

미완료 상태에는 `decision_status`를 만들지 않는다. 문서·UI의 임의 미실행 별칭은 Evaluation 공통 저장 Enum으로 사용하지 않는다. Required 하위 항목에 미완료가 하나라도 있으면 부모 Run·Suite·Gate·Profile의 판정도 `null`이고 모든 차단 실행 상태를 `blocking_execution_statuses[]`에 보존한다. 모든 Required 항목이 `COMPLETED`일 때만 `FAIL > INCONCLUSIVE > PASS` 순으로 판정을 집계한다.

평가 결과는 로컬 또는 CI artifact로 보존하고 저장소에는 합성 Dataset·Schema·Runner와 비민감 요약만 commit한다. 실제 환자 질문·답변, 의료 원문 전체, Judge reasoning 전문, Provider body와 credential은 결과 artifact·로그에 저장하지 않는다.

## 후보 Bundle 평가 Guard

보호된 Local Runner는 Run 시작 시 `EVALUATION_CANDIDATE` Guard로 정확한 `BUILDING` Bundle ID·Manifest Hash의 전체 Source·Snapshot·승인·Freshness·Scope Policy·Revocation 무결성을 검사한다. 환경 Active Pointer나 환자 공개 경로를 사용하지 않는다.

각 Required Case는 `EVALUATION_REQUEST` Guard로 실제 Evidence Selection과 요청 Scope를 환자용 `REQUEST`와 같은 규칙으로 검사하고 같은 Run의 `EVALUATION_CANDIDATE/PASS`에 결속한다. Candidate Guard의 Bundle ID·Manifest Hash·Revision·Epoch 또는 Case 기대 Scope와 하나라도 다르면 `execution_status=INVALID`, `decision_status=null`로 처리한다. 구조는 일치하지만 승인·Freshness·Scope 정책 판정 자체가 실패한 완료 Case만 `COMPLETED/FAIL`이 될 수 있다.

평가 결과는 Run Artifact에만 저장하며 환자 API·Application Chat/Guide 결과에 공개하지 않는다. 모든 Required Case Guard, 동일 Manifest의 필수 평가와 독립 승인이 `PASS`한 뒤에만 Bundle을 `READY`로 전환할 수 있다. 일반 환자 `REQUEST`는 `BUILDING` Bundle을 사용할 수 없다.

## 최소 검증

- 동일 Dataset·Gold·Metric version의 Baseline/Candidate 비교
- `AUTHORING | DEV | HOLDOUT | SAFETY_REGRESSION` Partition과 Leakage Group 교차 배치 차단
- 분석 단위·Cluster 차원의 독립 Group 수와 최소 Case·95% 신뢰구간 정책 Freeze
- Retrieval과 Answer·Citation·Safety Metric 분리
- HOLDOUT·SAFETY_REGRESSION 필수 실행, 미실행 `NOT_EVALUATED/null`과 실행 완료 분모 0 `COMPLETED/INCONCLUSIVE` 구분
- Critical 0건 Gate의 평균 점수 우회 금지
- Rule 양성·Rule 없음·승인된 선행 차단에 따른 Rule 미실행과 근거 부족·상충 Case
- OTC Identity 불충분은 Evaluation Case에 복제하지 않고 상류 Candidate/Resolver Contract Receipt로 차단
- Prompt Injection, Citation 변조, 검색 근거 밖 Claim
- 비활성·만료·미승인 Source 사용 차단
- 처방·Identification·Bundle 변경의 `STALE`
- 상류 Contract Receipt와 RAG Metric 결과 분리
- Dataset·Source·Index·Bundle·Execution·Model·Prompt·Parser·Embedding·commit 재현
- Run Candidate Guard와 모든 Required Case Request Guard의 동일 Bundle·Manifest·Revision·Epoch 결속
- 평가 결과의 환자 API·Application 결과 저장·공개 0건
- 실제 환자정보·credential·원문 Provider payload의 Dataset·결과·로그 미포함

## 공개 게이트

필수 평가의 `execution_status`가 `NOT_IMPLEMENTED | NOT_EVALUATED | INVALID | ERROR`이거나 완료 판정이 `FAIL | INCONCLUSIVE`이면 `PUBLIC_TRACK_F=false`를 유지한다. 구현 PR 담당 리뷰어와 의료·약학·Source·Privacy·Safety 승인 전에는 합성 fixture를 사용하는 접근 통제된 로컬 데모만 허용한다.
