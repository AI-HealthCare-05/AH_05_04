# Issue #273 자연어 합성 질문 기반 실제 Retrieval 평가 설계

## 1. 상태와 범위

- Issue: `#273`
- 브랜치: `issue-273-natural-language-retrieval`
- 구현 담당자: 정현우 (`@ceohwj`)
- 담당 리뷰어: 권가빈 (`@hazelnutflavoured`)
- Retrieval 통합 리뷰: `#178`에 지정된 Evidence·Scope·Safety 리뷰어
- 현재 단계: 공개 가능한 DEV Dataset·Gold·Leakage·검증 준비
- 현재 차단 상태: `BLOCKED_BY_RAG_14_ADAPTER`, `WAITING_FOR_HOLDOUT_FREEZE`
- 공개 게이트: `PUBLIC_TRACK_F=false`

이 설계는 자연어 합성 질문으로 실제 Knowledge Evidence Retriever의 검색 품질을 측정하기 위한 입력과
실행 경계를 정의한다. 첫 구현 단계에서는 DEV 60개와 검증 가능한 Gold graph를 저장소에 추가한다.
HOLDOUT 40개의 질문 본문과 실제 Retriever 성능 수치는 승인된 접근 통제와 실제 Adapter가 준비되기 전에는
생성하거나 공개하지 않는다.

## 2. 정본과 현재 구현 상태

다음 자료를 우선한다.

1. `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
2. `docs/governance/decisions/2026-09-03-rag-evaluation-schema-set-1-2-freeze.md`
3. `docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-design.md`
4. `docs/designs/ceohwj/issue-178-rag-evidence-retrieval-design.md`
5. `docs/privacy-safety.md`, `docs/testing.md`
6. GitHub Issue `#273`

현재 `develop`에는 다음 기반이 병합돼 있다.

- PR `#262`: DEV Evaluation Runner·Reporter 구현
- PR `#270`: `#178` Knowledge Evidence Retrieval Kernel과 Port 계약
- PR `#274`: `#158` Retrieval Metric·synthetic replay DEV baseline

그러나 `#157`은 승인된 HOLDOUT Baseline과 Freeze Receipt가 남아 있어 Open이고, `#178`은 실제
`EvidenceSearchPort`·rerank Adapter와 versioned Knowledge Evidence Index 연결이 없다. 따라서 이번 단계에서
실행 가능한 것은 Dataset authoring graph와 정적 검증까지다. Kernel fake Port나 replay 순위를 실제 검색 결과로
재해석하지 않는다.

## 3. 목표와 비목표

### 목표

1. 다섯 Retrieval 주제별 12개, 총 60개의 한국어 자연어 DEV 질문을 만든다.
2. 모든 Case를 required/relevant Gold Evidence와 Schema Set `1.2.0` provenance에 결속한다.
3. 표현 유형과 네 Leakage 축의 배분을 기계적으로 검증한다.
4. 실제 Adapter가 준비되면 기존 Metric Kernel로 top-5 품질을 계산할 수 있는 입력 경계를 만든다.
5. 저장소에서 검토할 수 있는 `docs/validation/rag/issue-273/report.md`에 DEV 준비 상태와 차단 조건을 남긴다.

### 비목표

- HOLDOUT 질문 본문을 일반 저장소, 공개 Issue, 일반 CI artifact에 저장
- replay 순위 또는 fake Port 결과를 `ACTUAL_RETRIEVAL_DEV`로 표시
- 실제 `EvidenceSearchPort`, PostgreSQL `pg_trgm`, vector search 또는 reranker 구현
- Answer·Citation·Safety Metric 또는 LLM Judge 실행
- 승인 전 threshold, 품질 `PASS`, Release 판정 또는 Production 공개
- 실제 의약품의 임상적 효능·용량·중단·복용 변경 주장을 평가 데이터에 포함

## 4. 검토한 접근과 결정

### 선택: 별도 자연어 DEV Dataset

`rag-retrieval-dev@1.0.0`을 수정하지 않고 `rag-natural-language-retrieval-dev@1.0.0`을 새로 만든다.
기존 Dataset은 `#158`의 replay 결정성 baseline이며 query token과 고정 순위라는 의미가 이미 고정돼 있다.
새 자연어 Case를 같은 version에 추가하면 기존 Run hash와 baseline 의미가 바뀌므로 별도 Dataset이 필요하다.

### 미선택: 기존 5개 DEV Case를 60개로 확장

기존 Case는 Metric·Reporter·Comparison 회귀 fixture다. 자연어 품질 평가와 결합하면 replay pipeline 회귀와
actual retrieval 품질을 다시 혼동하게 되므로 채택하지 않는다.

### 미선택: 100개를 한 번에 공개 저장

HOLDOUT 질문이 DEV 튜닝에 노출되고 Freeze 전 결과를 관찰할 수 있다. HOLDOUT 40개는 별도 접근 통제와
Freeze Receipt가 확정된 뒤 저장소 밖 보호 경로에서 작성한다.

## 5. Dataset identity와 파일 배치

| 항목 | 값 |
| --- | --- |
| Dataset code | `rag-natural-language-retrieval-dev` |
| Dataset version | `1.0.0` |
| 파일 prefix | `rag-natural-language-retrieval-dev-v1` |
| Scope | `SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_DEV` |
| Classification | `SYNTHETIC` |
| Partition | `DEV` 60, 나머지 0 |
| 초기 상태 | `DRAFT` |
| Schema Set | `rag-eval.schema-set@1.2.0` |
| Runtime eligible | `false` |

추적 대상 파일은 기존 `evals/` authoring graph 구조를 그대로 사용한다.

```text
evals/
├── policies/rag-natural-language-retrieval-dev-v1.*.json
├── profiles/rag-natural-language-retrieval-dev-v1.profile.json
├── provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json
├── retrieval/
│   ├── cases/rag-natural-language-retrieval-dev-v1/*.json
│   ├── evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json
│   ├── evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json
│   └── manifests/rag-natural-language-retrieval-dev-v1.*.json
└── suites/rag-natural-language-retrieval-dev-v1.suite.json
```

Case ID는 `rag-nlr-dev-001`부터 `rag-nlr-dev-060`까지 고정한다. 모든 JSON은 기존 canonical serializer와
content hash 규칙을 사용하며 수동으로 hash를 추정하지 않는다.

## 6. 질문 구성

### 주제 배분

| Topic tag | 설명 | DEV 수 |
| --- | --- | ---: |
| `TOPIC_MEDICATION_INFORMATION` | 합성 의약품의 일반 정보 | 12 |
| `TOPIC_PRECAUTIONS` | 합성 주의사항 | 12 |
| `TOPIC_LIFESTYLE_MANAGEMENT` | 합성 생활 관리 안내 | 12 |
| `TOPIC_STORAGE` | 합성 보관 방법 | 12 |
| `TOPIC_MISSED_DOSE` | 합성 복용 누락 안내 | 12 |
| **합계** |  | **60** |

### 표현 유형 배분

각 주제는 아래 여섯 표현 유형을 정확히 2개씩 가진다. 전체 Dataset에서는 유형별 10개가 된다.

| Expression tag | 설명 | 전체 수 |
| --- | --- | ---: |
| `EXPRESSION_CANONICAL` | 완결된 표준 질문 | 10 |
| `EXPRESSION_SYNONYM` | 의미를 보존한 동의어·유사 표현 | 10 |
| `EXPRESSION_WORD_ORDER_PARTICLE` | 조사 생략·어순 변화 | 10 |
| `EXPRESSION_COLLOQUIAL` | 일상 구어체 | 10 |
| `EXPRESSION_FRAGMENT` | 짧고 불완전하지만 의도가 식별되는 질문 | 10 |
| `EXPRESSION_LIMITED_TYPO` | 의미를 훼손하지 않는 단일 합성 오타 | 10 |
| **합계** |  | **60** |

질문은 한국어 자연문장이지만 실제 환자 발화나 Production traffic에서 가져오지 않는다. 합성 제품은
`합성의약품`임을 문장과 fixture에서 명시한 가상 이름만 사용한다. 실제 사람 이름, 연락처, 처방 식별자,
보험코드, 진료 내용, 실제 Provider payload는 사용하지 않는다.

## 7. 독립 Group과 Leakage

각 주제는 4개의 base intent를 가지며, 각 base intent에서 3개의 표현 변형을 만든다. 전체 20개
`transform_origin` group에 Case가 3개씩 속한다.

- `question_template`: 질문의 문법·의도 template 식별자
- `source_segment`: 정답을 지지하는 합성 Evidence segment 식별자
- `medication_family`: 가상 의약품 family 식별자
- `transform_origin`: 같은 base intent에서 파생된 표현 묶음

같은 `transform_origin`의 세 Case는 통계적으로 독립이라고 간주하지 않는다. 실제 평가의 bootstrap
`independence_unit`과 `cluster_dimension`은 `transform_origin`으로 설정하고 최소 독립 Group 수는 20으로
고정한다. 주제·표현 유형별 결과는 diagnostic slice이며 2개짜리 세부 cell에 독립적인 품질 PASS를 부여하지
않는다.

현재 Dataset에는 DEV만 있으므로 cross-partition leakage는 발생할 수 없지만, 미래 HOLDOUT graph와 비교할 수
있도록 네 축의 ID namespace를 DEV 전용으로 고정한다. HOLDOUT은 어떤 축에서도 DEV ID를 재사용하지 않는다.

## 8. Gold Evidence와 합성 Index

60개 Case는 20개의 base intent에 대응하는 최소 20개의 required `KNOWLEDGE_CHUNK`를 가진다. 필요한 경우
같은 의도를 보완하는 detail chunk를 relevant Evidence로 추가할 수 있지만 required와 relevant 집합은
Case마다 명시한다.

Gold 작성 원칙은 다음과 같다.

- required Evidence는 질문에 답하기 위해 반드시 검색돼야 하는 최소 chunk다.
- relevant Evidence는 required를 포함하며 보완 설명 chunk를 추가할 수 있다.
- locator는 합성 Index JSON의 안정적인 record 위치를 가리킨다.
- Evidence mapping과 resource content hash는 canonical byte에 결속한다.
- 실제 허가정보, 복약안내문, 의료 문구 또는 라이선스가 필요한 Source passage를 복사하지 않는다.
- 합성 문구는 검색 일치 여부를 평가할 만큼 의미가 분리되지만 임상적 사실처럼 해석되지 않도록 표시한다.

Gold와 Dataset은 처음에는 `DRAFT`다. 실제 reviewer identity, 검토 시각, immutable review evidence가 생기기
전에는 `REVIEWED`나 `APPROVED`를 기록하지 않는다. 작성자의 self-approval은 허용하지 않는다.

## 9. 검증 설계

`ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py`가 최소 다음을 검증한다.

1. Schema Set `1.2.0` graph 전체가 `load_dataset()`을 통과한다.
2. `DEV=60`, 다섯 주제별 12개, 여섯 표현 유형별 10개가 exact-match한다.
3. 모든 Case가 `RETRIEVAL`, `SYNTHETIC`, `DRAFT`이며 required Evidence를 하나 이상 가진다.
4. query가 `SYNTHETIC_QUERY_*` token이 아니라 한국어 자연문장이다.
5. 가상 제품 allowlist 밖의 실제 제품명과 개인정보 sentinel이 없다.
6. 네 Leakage 축이 비어 있지 않고 Case별 배분 및 DEV namespace 규칙을 만족한다.
7. `transform_origin`은 정확히 20개이며 각 group은 3개 Case를 가진다.
8. 모든 Gold ref가 Evidence mapping에 존재하고 required가 relevant의 부분집합이다.
9. Case input hash, resource hash, graph manifest hash와 protected receipt가 canonical 값과 일치한다.
10. HOLDOUT Case 또는 HOLDOUT 질문 resource가 일반 저장소 경로에 없다.

추가 negative test는 수량·주제·표현 유형·Gold ref·Leakage group·민감정보·hash 중 하나가 변조될 때 안정된
validation code로 실패하고 질문 원문을 오류에 노출하지 않는지 확인한다.

## 10. 실제 Retrieval 연결 경계

실제 평가는 `#178`의 concrete `EvidenceSearchPort`와 rerank Adapter가 versioned Knowledge Evidence Index에
연결된 뒤에만 수행한다. Evaluation Adapter는 다음 변환만 소유한다.

1. DEV Case query를 transient `SensitiveText`와 승인된 query fingerprint로 만든다.
2. Case와 고정된 Index·filter·retrieval·lexical·dense·rerank config ref를 Kernel request에 결속한다.
3. 실제 Port가 반환한 선택 결과의 `evidence_key`를 Case별 ranked Evidence ID로 변환한다.
4. 기존 Retrieval Metric Kernel에 top-5를 전달한다.
5. raw query, Evidence content, Provider body를 Run artifact나 일반 로그에 저장하지 않는다.

Kernel fake Port, replay JSON 또는 수동 순위는 이 경로에서 금지한다. 실제 Adapter가 없으면 실행 상태를
`NOT_EVALUATED`, 판정을 `null`로 유지하고 `BLOCKED_BY_RAG_14_ADAPTER`를 기록한다.

## 11. Metric과 report

실제 DEV 실행은 기존 `#158` Metric 정의를 재사용한다.

- Recall@5
- Precision@5
- MRR
- nDCG@5
- No-hit Rate
- numerator/denominator와 95% cluster bootstrap CI

실제 실행 결과의 기계 정본은 기존처럼 `evals/results/<run-id>/`에 생성한다. 이 디렉터리는 Git 비추적이며
다음 파일을 포함한다.

```text
evals/results/<run-id>/
├── run.json
├── cases.jsonl
├── metrics.json
├── suite-results.json
├── failures.jsonl
├── report.md
└── result-content-manifest.json
```

Reporter는 실제 Adapter 실행을 `ACTUAL_RETRIEVAL_DEV`로 표시한다. replay 결과의
`SYNTHETIC_REPLAY_DEV`와 혼용하지 않는다. `report.md`는 JSON의 비정본 projection이며 수정해도 기계 판정이나
semantic hash가 바뀌지 않는다.

저장소에 추적하는 `docs/validation/rag/issue-273/report.md`는 다음 항목만 담는 비민감 진행·검증 보고서다.

- Dataset identity와 Case/주제/표현/독립 Group 수
- Dataset·Gold·Index·config·Git revision hash
- 실행한 검증 명령과 결과
- Gold review와 HOLDOUT Freeze 상태
- actual Adapter 실행 여부와 차단 코드
- 실제 Run이 생긴 뒤의 Run ID·semantic hash·비민감 Metric 요약
- DEV 결과는 Release PASS가 아니라는 명시적 해석 제한

Adapter 실행 전에는 Metric 값을 `0`으로 채우지 않고 `NOT_EVALUATED/null`로 기록한다.

## 12. HOLDOUT 경계

HOLDOUT 40개는 주제별 8개로 구성하되 이 설계의 일반 저장소 변경에 포함하지 않는다. 준비 전에 다음이
별도 승인돼야 한다.

- 보호 저장 위치와 접근 주체
- 작성자·검토자·Dataset Custodian 역할 분리
- DEV와 네 Leakage 축이 겹치지 않는 검증 방식
- immutable content hash와 Freeze Receipt 형식
- 최초 실행 승인과 결과 접근 기록
- threshold, 최소 Case/독립 Group, estimator와 CI policy

승인 전에는 HOLDOUT 질문을 생성·열람·실행하지 않는다. 승인 부재는
`WAITING_FOR_HOLDOUT_FREEZE`이며, DEV 결과로 이를 우회하지 않는다.

## 13. 실패·안전 처리

- Dataset graph 불일치: 실행 전 `INVALID/null`
- Gold 미검토: actual 평가와 품질 판정 차단
- Adapter 부재: `NOT_EVALUATED/null`, `BLOCKED_BY_RAG_14_ADAPTER`
- Index·config·Git revision 불일치: `INVALID/null`
- Case Adapter 오류: 해당 Case `ERROR/null`, 비민감 reason code만 기록
- 최소 Case·독립 Group 부족: `COMPLETED/INCONCLUSIVE`
- 승인 threshold 부재: Metric은 diagnostic으로 기록하되 품질 PASS 금지
- 개인정보·credential·실제 의료 데이터 가능성: 작성 또는 실행 즉시 중단

실패 report와 `failures.jsonl`에는 Case ID, stage, 안정 reason code만 기록한다. query 원문, Evidence content,
exception message, SQL parameter, Provider payload는 기록하지 않는다.

## 14. 단계별 전달 계획

### 단계 A — DEV authoring

- DEV 60개, 합성 Evidence Index, Gold mapping과 authoring graph 작성
- exact distribution·privacy·leakage·hash 검증 추가
- `docs/validation/rag/issue-273/report.md`에 검증 결과와 차단 상태 기록

### 단계 B — Gold review

- 담당 리뷰어의 실제 검토 evidence 확보
- 별도 변경에서 `DRAFT → REVIEWED | APPROVED` 전이
- 승인 event 없이 상태를 미리 변경하지 않음

### 단계 C — actual Adapter integration

- `#178` concrete Adapter와 versioned Index Receipt 확인
- Evaluation Adapter 연결 및 DEV 60개 실행
- 동일 입력 반복 실행의 semantic hash 또는 허용된 결정성 Receipt 비교
- 실제 Metric·failure 요약을 validation report에 갱신

### 단계 D — HOLDOUT preparation and run

- 별도 접근 통제 아래 40개 작성·검토·Freeze
- 승인된 policy 이후 최초 실행
- DEV와 HOLDOUT 결과를 분리하고 최종 Release 입력은 후속 Gate에 전달

## 15. 검증 명령

단계 A의 최소 검증은 다음과 같다.

```bash
UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q
UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run mypy ai_worker/tasks/evaluation
git diff --check
```

단계 C에서는 `ai_worker/tests/rag`와 동일 입력 반복 actual DEV Run을 추가한다. Adapter가 없거나 보호된
HOLDOUT 실행 승인이 없으면 해당 검증을 통과로 보고하지 않고 차단 상태를 유지한다.

## 16. 완료 조건

단계 A 완료는 다음 조건으로 제한한다.

- DEV 60개와 Gold authoring graph가 Schema Set `1.2.0`으로 저장된다.
- 주제·표현 유형·Leakage·privacy·hash 검증이 통과한다.
- 저장소 validation report가 실제 실행 여부와 차단 상태를 정확히 표현한다.
- HOLDOUT 본문과 실제 Retrieval Metric은 생성되지 않는다.

Issue `#273` 전체 Close에는 추가로 다음이 필요하다.

- HOLDOUT 40개의 승인된 Freeze·접근 통제 증빙
- query replay가 아닌 `#178` 실제 Adapter 호출
- Dataset·Gold·Index·config·Git revision에 결속된 Metric과 failure artifact
- 동일 입력 반복 실행의 결정성 또는 승인된 허용 범위 증빙
- 담당 리뷰어의 threshold·HOLDOUT 사용 승인 기록

이 평가만으로 임상적 유효성, Answer 안전성 또는 Production 공개 가능성을 주장하지 않는다.
