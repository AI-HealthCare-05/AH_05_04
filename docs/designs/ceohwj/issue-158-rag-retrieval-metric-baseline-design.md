# Issue #158 RAG Retrieval Metric·DEV Baseline 설계

## 1. 목적

Issue #158은 `KNOWLEDGE_RETRIEVAL` 실험에서 동일한 합성 DEV Dataset·Gold·Filter·Source/Index 조건으로
Retrieval Baseline과 Candidate를 실행하고, 재현 가능한 Retrieval Metric과 비교 Artifact를 생성한다.

이 작업이 만드는 결과는 개발 중 검색 변경을 비교하기 위한 DEV diagnostic evidence다. HOLDOUT Baseline
Freeze, Release PASS, 임상적 유효성 또는 Production 공개 승인을 의미하지 않는다.

## 2. 구현 범위

- Baseline variant: `RET-L` synthetic lexical replay
- Candidate variant: `RET-HR` synthetic hybrid-plus-rerank replay
- Dataset: 합성 DEV Retrieval 전용 5 Case와 서로 다른 `question_template` 독립 Group 5개
- Metric: Recall@5, Precision@5, MRR, nDCG@5, No-hit Rate
- CI: 고정 seed를 사용하는 case/group percentile bootstrap 95% CI
- 결과: Run별 JSON Bundle과 Markdown Projection
- 비교: 동일 통제 변수 확인, baseline/candidate 값과 delta 기록
- 실패: 분모 0, 최소 Case/Group 부족, replay 누락, 실행 오류, 통제 변수 불일치를 fail-closed로 기록

Answer, Citation, Safety, LLM Judge, OCR Candidate Resolver 품질, HOLDOUT 실행과 Release Gate는 제외한다.

## 3. 핵심 경계

### 3.1 Retrieval과 Resolver 분리

Retrieval Case의 Gold는 안정 `evidence_ref_id`만 사용한다. OCR Candidate Search·Identification·Resolver 결과는
Case, Metric numerator/denominator 또는 비교 점수에 포함하지 않는다. 상류 상태는 기존
`upstream_contract_manifest_hash`로만 연결한다.

### 3.2 Replay Adapter와 실제 Retrieval Adapter 분리

이번 구현의 저장소 내 기준 결과는 승인 가능한 합성 replay fixture로 만든다. Metric Kernel은
`RetrievalCaseResult`만 소비하므로 향후 Issue #178 Evidence Retrieval Kernel Adapter가 연결되어도 Metric 계산과
Artifact 계약은 바뀌지 않는다.

Replay fixture는 검색 품질 주장 자체가 아니라 Metric·Reporter·Comparison 파이프라인의 결정성과 실패 처리를
검증하는 입력이다. `report.md`에는 `SYNTHETIC_REPLAY_DEV`를 명시한다.

### 3.3 계약 변경 제한

기존 `rag-eval.run`, `rag-eval.case-result`, `rag-eval.metrics`, `rag-eval.comparison`,
`rag-eval.content-manifest` 1.0.0을 사용한다. 공유 Schema의 필드·enum·requiredness는 변경하지 않는다.

Source/Index/embedding/parser/filter provenance는 versioned execution config 안의 공통 `model_config`에
명시하고, `run.json.model_config_hash`, `retrieval_variant_manifest_hash`, `resolved_evaluation_config_hash`, Git
commit SHA로 결속한다. 두 variant의 `model_config_hash`는 같고 retrieval strategy·reranker 설정과 replay
artifact만 달라진다.
`comparison.json`은 두 Run의 semantic content hash와 통제 변수 hash 일치 여부를 기록한다.
비교 가능한 두 Run은 모두 `KNOWLEDGE_RETRIEVAL`이고 같은 `experiment_id`에 속해야 하며, Run ID·variant ID·
retrieval variant manifest hash는 서로 달라야 한다. 이 pair identity 불변식은 CLI 사전 검사와 comparison
builder 직접 호출 경로에서 동일하게 적용하고, 위반 시 `EVAL_STATE_COMBINATION_INVALID`로 닫는다.
발행된 candidate bundle을 다시 읽을 때는 참조된 baseline Run도 같은 result root에서 안전하게 로드해 baseline
Run ID·semantic hash·통제 변수 hash·metric 값을 `comparison.json`과 대조한다.

## 4. 데이터와 실행 입력

새 Dataset `rag-retrieval-dev@1.0.0`은 정확히 5개의 `RETRIEVAL/DEV/SYNTHETIC` Case를 가진다. 각 Case는
required evidence 하나와 relevant evidence 하나 이상을 가지며 `question_template` Group이 서로 다르다.
Authoring graph의 8개 provenance-aware member는 Schema Set 1.2를 사용하고 실제 review evidence가 없으므로
`team_gold_status=DRAFT`, reviewer·approver·review timestamp는 `null`로 유지한다.

Baseline과 Candidate config는 다음 통제 변수를 동일하게 유지한다.

- Dataset Manifest
- Gold/Evidence Mapping
- Comparison/Evaluation Policy
- DEV Partition과 Case 순서
- Source Snapshot
- Knowledge Index Snapshot
- Filter Snapshot
- Parser와 embedding identity
- top-k = 5
- bootstrap seed와 iteration 수

Comparison builder는 `CASE_SET`, `DATASET`, `GOLD`, `METRIC_POLICY`, `SOURCE_INDEX_FILTER_MODEL`의 정렬된 전체
서명을 exact match로 요구한다. 일부 key 생략·중복·순서 변경·미지원 key는 provenance 검사를 약화시키므로
`EVAL_STATE_COMBINATION_INVALID`로 거부한다.

의도적으로 달라지는 값은 retrieval strategy와 reranker 사용 여부뿐이다.

## 5. Metric 정의

Case별 Top-5는 순서를 보존하고 중복 Evidence ID를 허용하지 않는다.

- Recall@5: Top-5에 포함된 required evidence 수 / required evidence 전체 수
- Precision@5: Top-5 relevant evidence 수 / 5. 반환 결과가 5개보다 적으면 빈 slot은 non-relevant로 계산
- MRR: 첫 relevant evidence의 reciprocal rank, 없으면 0
- nDCG@5: binary relevance DCG@5 / 동일 relevant 수의 ideal DCG@5
- No-hit Rate: relevant evidence가 Top-5에 하나도 없는 Case 수 / 평가 Case 수

`MetricResult.numerator/denominator`는 Recall·Precision·No-hit의 직접 비율 count를 저장한다. MRR·nDCG는
non-zero Case 수 / 전체 Case 수를 관측 support count로 저장하고, `metric_value`는 Case score의 산술평균을
저장한다. 이 estimator 의미는 Metric version `1.0.0`과 report에 함께 명시한다.

모든 point estimate와 CI bound는 소수점 여섯 자리에서 `ROUND_HALF_EVEN`으로 canonicalize한다.
CI는 `question_template` 독립 Group을 sampling unit으로 하는 percentile bootstrap이며 seed와 iteration 수를
Comparison Policy에서 읽는다. Group이 5개 미만이거나 Case가 5개 미만이면 Metric을 계산 완료하되
`decision_status=INCONCLUSIVE`와 안정 reason code를 기록한다. 분모 0도 동일하게 `INCONCLUSIVE`이며 PASS가
될 수 없다. Kernel은 Metric version, estimator ID/version, CI method ID/version·parameter shape,
unit/independence/cluster 설정을 포함한 전체 알고리즘 서명을 검증하고, 지원하지 않는 서명은 계산하지 않고
`NOT_IMPLEMENTED/null`로 닫는다.

## 6. 실행과 비교 흐름

1. `run-dev`가 config·Dataset graph·Git clean state를 검증한다.
2. `retrieval-replay.v1` Adapter가 replay 행의 `case_resource_sha256`과 실행 Case resource hash를 exact match한
   뒤 Case별 ranked Evidence ID를 `RetrievalCaseResult`로 변환한다.
3. Metric Kernel이 Policy Scope별 Metric과 CI를 계산한다.
4. Baseline Run을 `evals/results/<baseline-run-id>/`에 원자적으로 발행한다.
5. Candidate 실행은 `--baseline-run-id`로 Baseline Bundle을 읽고 semantic content hash를 검증한다.
6. 통제 변수가 일치하면 delta를 만들되, 승인된 Release threshold가 없으므로 비교 판정은
   `INCONCLUSIVE`로 유지한다.
7. Candidate Bundle에 `comparison.json`을 포함해 새 디렉터리로 원자 발행한다.

Candidate semantic hash는 기존 semantic payload인 `run.json`, `cases.jsonl`, `metrics.json`,
`suite-results.json`, `failures.jsonl`로 계산한다. `comparison.json`과 `report.md`는 semantic hash에서 제외해
자기 참조 순환을 만들지 않는다. `FailureRecord.created_at`도 Run 시각과 마찬가지로 semantic projection에서
제외한다.

## 7. 결과 저장

Baseline Bundle:

```text
evals/results/<baseline-run-id>/
├── run.json
├── cases.jsonl
├── metrics.json
├── suite-results.json
├── failures.jsonl
├── report.md
└── result-content-manifest.json
```

Candidate Bundle:

```text
evals/results/<candidate-run-id>/
├── run.json
├── cases.jsonl
├── metrics.json
├── suite-results.json
├── comparison.json
├── failures.jsonl
├── report.md
└── result-content-manifest.json
```

`evals/results/`는 기존 `.gitignore` 규칙을 유지한다. 로컬 결과는 프로젝트 폴더에 보존하고, CI는
`rag-evaluation-<run-id>` Artifact로 업로드한다. 소스 PR에는 결과 전체를 커밋하지 않고 Run ID, semantic
hash, Dataset/Policy hash와 비민감 요약만 남긴다. Candidate 검증은 참조 baseline bundle을 같은 result root에서
resolve하므로, Artifact를 별도로 보존했다면 두 Run ID 디렉터리를 같은 root 아래 복원한다.

## 8. 오류와 상태

- Replay Case 누락·중복·알 수 없는 Case: `INVALID/null`
- Replay Case resource hash 불일치: 해당 Case와 aggregate Run `INVALID/null`
- Adapter가 반환한 retrieved/selected ranked ID 중복: 해당 Case와 aggregate Run `INVALID/null`, 안정 failure 기록
- 지원하지 않는 Metric algorithm signature: 해당 Metric `NOT_IMPLEMENTED/null`
- Adapter 예외: 해당 Case `ERROR/null`, 다음 Case는 계속 실행
- required/relevant evidence 분모 0: Metric `COMPLETED/INCONCLUSIVE`
- 최소 Case·독립 Group 부족: Metric `COMPLETED/INCONCLUSIVE`
- Baseline Bundle 누락·Schema/hash 오류: 비교 미발행, CLI 실패
- 통제 변수 불일치: `comparison.json`을 `INVALID/null`로 기록
- 승인된 threshold 부재: delta를 기록하되 comparison은 `COMPLETED/INCONCLUSIVE`
- 실제 RAG-07A/07B/08 연결 부재: report에 `BLOCKED_BY_RAG_07A_07B_OR_08`을 기록하되 합성 DEV Metric 실행은 허용

## 9. 검증 기준

- 수작업으로 계산한 5 Case Metric과 구현 결과가 일치한다.
- 동일 fixture를 서로 다른 Run ID로 두 번 실행한 semantic content hash가 같다.
- baseline/candidate의 통제 변수 불일치는 비교를 `INVALID`로 만든다.
- 분모 0과 표본 부족은 PASS가 아니다.
- 실패 Case ID와 비민감 reason code가 `failures.jsonl`과 report에 나타난다.
- Result Bundle 파일 hash와 content manifest가 일치한다.
- 기존 validation-only 및 Answer/Safety DEV 실행 동작은 회귀하지 않는다.
- `evals/results/` 결과는 Git 추적 대상이 아니다.
