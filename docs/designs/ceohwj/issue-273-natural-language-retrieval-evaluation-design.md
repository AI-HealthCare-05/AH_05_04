# Issue #273 자연어 합성 질문 기반 실제 Retrieval 평가 설계

## 1. 상태와 범위

- Issue: `#273`
- 브랜치: `issue-273-natural-language-retrieval`
- 구현 담당자: 정현우 (`@ceohwj`)
- 담당 리뷰어: 권가빈 (`@hazelnutflavoured`)
- Retrieval 통합 리뷰: `#178`에 지정된 Evidence·Scope·Safety 리뷰어
- 현재 단계: Phase 0 Evaluation provenance 계약 확장 설계와 공개 DEV 질문 matrix 준비
- 현재 차단 상태: `BLOCKED_BY_EVAL_SCHEMA_EXTENSION`, `BLOCKED_BY_RAG_14_ADAPTER`,
  `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`, `WAITING_FOR_HOLDOUT_FREEZE`
- 공개 게이트: `PUBLIC_TRACK_F=false`
- 연계 범위: 증상 기반 OTC 후보·처방약–OTC 상호작용 평가는 별도 Issue로 분리

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
실행 가능한 것은 질문 matrix와 Schema Set `1.2.0` 호환 Case 초안의 준비까지다. provenance 확장 후보는
[`rag-eval.schema-set@1.3.0`](../../governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md),
SHA-256 `e9843e190fbfabc6305d709e04ea296aefd107e66739882471fa3aedee08092f`이며 상태는
`Candidate · Review Required`다. 책임 리뷰어의 실제 Pull Request review event 전에는 승인된 Schema Set이나
최종 Dataset graph 입력으로 취급하지 않고 Freeze하지 않는다. Kernel fake Port나 replay 순위를 실제 검색
결과로 재해석하지 않는다.

현재 구현과 목표 설계 사이의 차이는 다음처럼 명시적으로 닫는다.

| 항목 | 현재 구현 | #273 전이 조건 |
| --- | --- | --- |
| Leakage schema | 네 축에 `transform_origin` 존재 | Metric algorithm signature가 `transform_origin` cluster를 지원하고 회귀 테스트 통과 |
| Dataset provenance | Schema Set `1.2.0`의 18개 member | `rag-eval.schema-set@1.3.0` 후보의 책임 리뷰어 Pull Request review event |
| Retrieval Adapter | `retrieval-replay.v1`만 등록 | `knowledge-evidence-retrieval.actual.v1` 등록과 실제 Index Receipt 검증 |
| Reporter source label | `SYNTHETIC_REPLAY_DEV` 또는 generic `ADAPTER_EXECUTION_DEV` | 검증된 actual Adapter에만 `ACTUAL_RETRIEVAL_DEV` projection 추가 |
| protected execution | repository-root `run-dev`만 지원 | #157의 공통 component를 재사용하되 전용 protected Retrieval 실행 Issue에서 권한·audit·runner 구현 |

## 3. 목표와 비목표

### 목표

1. 다섯 Retrieval 주제별 12개, 총 60개의 한국어 자연어 DEV 질문을 만든다.
2. 모든 Case를 required/relevant Gold Evidence와 Phase 0에서 승인된 다음 Schema Set provenance에 결속한다.
3. 표현 유형과 네 Leakage 축의 배분을 기계적으로 검증한다.
4. 실제 Adapter가 준비되면 기존 Metric Kernel로 top-5 품질을 계산할 수 있는 입력 경계를 만든다.
5. 저장소에서 검토할 수 있는 `docs/validation/rag/issue-273/report.md`에 DEV 준비 상태와 차단 조건을 남긴다.
6. 실제 검색 corpus에 hard negative를 포함해 단순 query–Gold 문구 정렬만으로 높은 점수가 나오지 않게 한다.

### 비목표

- HOLDOUT 질문 본문을 일반 저장소, 공개 Issue, 일반 CI artifact에 저장
- replay 순위 또는 fake Port 결과를 `ACTUAL_RETRIEVAL_DEV`로 표시
- 실제 `EvidenceSearchPort`, PostgreSQL `pg_trgm`, vector search 또는 reranker 구현
- Answer·Citation·Safety Metric 또는 LLM Judge 실행
- 승인 전 threshold, 품질 `PASS`, Release 판정 또는 Production 공개
- 실제 의약품의 임상적 효능·용량·중단·복용 변경 주장을 평가 데이터에 포함
- 증상만으로 OTC 제품을 최종 추천하거나 처방약–OTC 상호작용 안전성을 판정

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

### 별도 Issue #278: 증상 기반 OTC 후보·상호작용 평가

`#273`의 100개는 Knowledge Evidence Retrieval만 소유한다. 아래 OTC 50개는 제품 후보 제시 전에 증상
위험도, 현재 복용약, 금기, 동일 성분 중복과 Rule Evidence를 확인해야 하므로 별도 Issue와 별도 Dataset으로
관리한다. 추적과 승인 기록은
[#278](https://github.com/AI-HealthCare-05/AH_05_04/issues/278)에서 소유하며, `#278`의 차단 상태는
`#273`의 완료를 차단하지 않는다.

| 계획된 OTC 평가 범위 | DEV | HOLDOUT | 합계 |
| --- | ---: | ---: | ---: |
| 증상 기반 OTC 후보 | 18 | 12 | 30 |
| 처방약–OTC 상호작용 | 12 | 8 | 20 |
| **합계** | **30** | **20** | **50** |

`#278`의 범위와 수량이 책임 리뷰어에게 승인되면 두 평가를 합친 프로그램 전체 질문 수는 `DEV=90`,
`HOLDOUT=60`, 총 150개다. 승인 전에는 OTC 50개와 전체 150개를 모두 `PROPOSED`로 취급한다. OTC
Issue는 승인된
`증상 범주 → 허가 효능·효과 → 성분 → 제품` Catalog, 위험 신호에서 제품 제시 0건, Rule·Safety Gate,
Answer·Citation 검증과 의사·약사 상담 안내를 완료 조건으로 가진다. 상담 문구만 추가해 안전 판정을
대체하지 않는다.

`#278`은 2026-09-05에 `PLANNED/BLOCKED` 상태로 미리 생성했다. 실행 시점은 Issue의 Open/Closed 상태가
아니라 다음 승인 산출물로 결정한다.

| 단계 | 가장 빠른 시작 | 시작 조건 |
| --- | --- | --- |
| 범위·계약 설계 | 2026-09-05 | Issue 생성 완료. 질문 본문은 아직 작성하지 않음 |
| 공개 DEV 30개 작성 | 2026-09-14 | 증상 기반 OTC Product·Safety Decision, `#166` Catalog·Source Receipt, `#176` Identity 계약·fixture, `#177` Rule 계약·fixture, OTC record schema·profile 승인 |
| 실제 DEV 실행 | 2026-09-15 | `#176`·`#177` 실제 구현과 versioned artifact, `#180` 실제 OTC Runtime, `#159` Answer, `#160` Citation, `#161` Safety 평가기 준비 |
| 보호 HOLDOUT 20개 작성·Freeze | 가장 빨라도 2026-09-16 | DEV 결과·실패 taxonomy 검토, metric·threshold·sample/group 규칙 승인, protected root·접근권한·audit·Freeze Receipt·cross-dataset leakage validator, Product·Safety와 약사/의료 작성 승인 |
| 최종 실행·종료 | HOLDOUT Freeze 이후 승인된 단일 평가 창 | 고정된 동일 artifact로 DEV/HOLDOUT 실행, 치명적 안전 실패 0건, 전 도메인·약사/의료 리뷰 완료 |

표의 날짜는 현재 연계 Issue 계획에 따른 earliest date이며, 시작 조건이 충족되지 않으면 자동으로 순연한다.
따라서 일정만 도달했다는 이유로 다음 단계로 넘어가지 않는다. HOLDOUT 질문 본문·Gold·leakage sidecar는
항상 보호 경계 안에만 둔다.

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
| Authoring base | `rag-eval.schema-set@1.2.0` |
| Final Schema Set candidate | [`rag-eval.schema-set@1.3.0`](../../governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md), SHA-256 `e9843e190fbfabc6305d709e04ea296aefd107e66739882471fa3aedee08092f` · `Candidate · Review Required`; 책임 리뷰어 Pull Request review event 전에는 미승인 |
| Evaluation Profile runtime eligible | `false` |

추적 대상 파일은 기존 `evals/` authoring graph 구조를 그대로 사용한다.

```text
evals/
├── configs/rag-natural-language-retrieval-dev-actual-v1.execution.json  # 단계 C에서 추가
├── policies/rag-natural-language-retrieval-dev-v1.*.json
├── profiles/rag-natural-language-retrieval-dev-v1.profile.json
├── provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json
├── retrieval/
│   ├── cases/rag-natural-language-retrieval-dev-v1/*.json
│   ├── evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json
│   ├── evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json
│   ├── manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json  # Phase 0 승인 뒤 추가
│   └── manifests/rag-natural-language-retrieval-dev-v1.*.json
├── provenance/rag-natural-language-retrieval-dev-v1.index-build-receipt.json  # 단계 C에서 추가
└── suites/rag-natural-language-retrieval-dev-v1.suite.json

docs/validation/rag/issue-273/
├── status.json  # 비민감 진행 상태의 기계 입력
└── report.md    # status.json과 승인된 Run 요약에서 생성한 projection

ai_worker/tasks/evaluation/natural_language_retrieval_validation.py  # strict status model·report renderer
ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py
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

질문은 한국어 자연문장이지만 실제 환자 발화나 Production traffic에서 가져오지 않는다. 제품명은 실제 제품과
충돌하지 않는 reserved fixture namespace의 가상 display name만 사용하고 allowlist로 검증한다. 모든 query와
Evidence 본문에 같은 `SYNTHETIC` 표식을 반복해 lexical score를 왜곡하지 않으며, 합성 여부는 Case·resource
metadata에서 명시한다. 실제 사람 이름, 연락처, 처방 식별자, 보험코드, 진료 내용, 실제 Provider payload는
사용하지 않는다.

## 7. 독립 Group과 Leakage

각 주제는 4개의 base intent를 가지며, 각 base intent에서 3개의 표현 변형을 만든다. 전체 20개
`transform_origin` group에 Case가 3개씩 속한다.

- `question_template`: 질문의 문법·의도 template 식별자
- `source_segment`: 정답을 지지하는 합성 Evidence segment 식별자
- `medication_family`: 가상 의약품 family 식별자
- `transform_origin`: 같은 base intent에서 파생된 표현 묶음

같은 `transform_origin`의 세 Case는 통계적으로 독립이라고 간주하지 않는다. 실제 평가의 bootstrap
`independence_unit`과 `cluster_dimension`은 `transform_origin`으로 설정하고 최소 독립 Group 수는 20으로
고정한다. 현재 Metric Kernel은 `question_template` signature만 지원하므로 단계 C에서
`LeakageAxis.TRANSFORM_ORIGIN`을 additive signature로 추가하고 기존 `question_template` 동작을 회귀 검증한다.
이 지원이 없으면 모든 Metric은 `NOT_IMPLEMENTED/null`이며 actual baseline으로 사용할 수 없다.

각 Case의 `slice_ids`는 정렬된 `ALL`, Topic 1개, Expression 1개를 가진다. Comparison Policy는 다섯 Metric에
대해 다음 optional diagnostic scope를 정의한다.

| Scope | Case 수 | 최소 독립 Group 수 | 판정 용도 |
| --- | ---: | ---: | --- |
| `ALL` | 60 | 20 | DEV 전체 diagnostic |
| Topic 5개 각각 | 12 | 4 | 주제별 관찰 |
| Expression 6개 각각 | 10 | 10 | 표현 유형별 관찰 |

모든 scope는 `required=false`, `decision_basis=DIAGNOSTIC_ONLY`, `threshold=0`이다. 작은 세부 cell이나 DEV
결과에 품질 PASS를 부여하지 않는다.

현재 Dataset에는 DEV만 있으므로 Loader의 cross-partition leakage는 발생할 수 없다. 사람이 검토하는
Leakage ID는 partition별 namespace를 사용하되, 미래 HOLDOUT과 의미적으로 같은 원본을 다른 ID로 숨길 수
없도록 네 축마다 아래 partition-neutral canonical identity를 함께 정의한다.

- `question_template`: canonical template specification
- `source_segment`: Source Snapshot ref + locator + per-chunk content hash
- `medication_family`: reserved family fixture identity
- `transform_origin`: base-intent seed + authoring transform specification

보호 환경의 cross-dataset validator는 canonical identity를 승인된 HMAC algorithm/key version으로 digest해
비교한다. 어떤 축에서도 DEV/HOLDOUT 교집합을 허용하지 않는다. 공개 DEV 합성 derivation input은 아래
repository sidecar에 둘 수 있지만, HOLDOUT canonical identity input과 모든 HMAC digest 값은 protected root
밖으로 내보내지 않는다.

각 Case의 canonical identity 입력은 `rag-eval.authoring-identity-manifest@1.0.0` sidecar에 Case ID와 함께
저장한다. 공개 DEV sidecar는 repository manifest 경로에 두고, HOLDOUT sidecar는 protected root에만 둔다.
sidecar는 canonical derivation algorithm version, question template specification, Source/locator/chunk hash,
reserved medication family fixture identity, base-intent seed와 transform specification을 가진다. Dataset별
sidecar content hash는 protected artifact와 study split receipt에 결속하며 validator가 Case set과 exact-match
한다. 입력 누락, 중복 Case, derivation version 불일치 또는 hash 불일치는 Freeze 전 `INVALID/null`로 닫는다.

## 8. Gold Evidence와 합성 Index

60개 Case는 20개의 base intent에 대응하는 정확히 20개의 Gold `KNOWLEDGE_CHUNK`를 가진다. Version 1에서는
각 base intent의 required와 relevant 집합을 같은 단일 Gold chunk로 고정하고 optional detail chunk를 두지
않는다. 합성 검색 corpus는 Gold chunk 20개와 hard-negative chunk 80개, 총 100개 record를 가진다. 각 base
intent마다 다음 negative를 하나씩 둔다. 각 negative record는 `adversarial_for_transform_origin`과
`negative_type`을 가지며, 지정된 origin에 대해서만 adversarial negative임을 보장한다. 해당 origin의
relevant Gold 집합에 포함되지 않고 record ID와 per-chunk hash가 유일해야 한다.

- 같은 가상 medication family의 다른 attribute
- 같은 Topic의 다른 medication family
- lexical overlap이 크지만 질문을 지지하지 않는 near match
- 다른 Topic의 용어가 일부 겹치는 cross-topic match

Gold 작성 원칙은 다음과 같다.

- required Evidence는 질문에 답하기 위해 반드시 검색돼야 하는 최소 chunk다.
- Version 1의 relevant Evidence는 required Evidence와 동일한 단일 chunk다.
- locator는 합성 Index JSON의 안정적인 record 위치를 가리킨다.
- Evidence mapping과 resource content hash는 canonical byte에 결속한다.
- 실제 허가정보, 복약안내문, 의료 문구 또는 라이선스가 필요한 Source passage를 복사하지 않는다.
- 합성 문구는 검색 일치 여부를 평가할 만큼 의미가 분리되지만 임상적 사실처럼 해석되지 않도록 표시한다.
- 질문 작성자와 Gold·negative 검토자는 분리하고 실제 review event 전에는 검토 provenance를 채우지 않는다.

이 100개 record는 DEV 전용 Index가 아니라 DEV와 미래 HOLDOUT이 함께 사용하는 study-wide immutable
corpus다. 20개는 DEV Gold이고 나머지 80개 전체 집합은 DEV Gold 20개 합집합과 disjoint하다. 각 negative의
adversarial 성질은 지정된 origin에만 적용하며 다른 origin에서의 난이도나 관련성까지 보장하지 않는다.
HOLDOUT Gold는 이 100개 안의 기존 record만 참조하며, 어떤 record가 HOLDOUT Gold인지는 보호 경계 밖에
공개하지 않는다.
HOLDOUT 작성 중 기존 corpus로 표현할 수 없는 Gold가 필요하면 Dataset과 Index를 `1.1.0`으로 올리고 같은
최종 Index·config로 DEV를 다시 실행한다. 서로 다른 Index의 DEV/HOLDOUT 점수는 직접 비교하거나 합산하지
않는다.

Gold와 Dataset은 처음에는 `DRAFT`다. 실제 reviewer identity, 검토 시각, immutable review evidence가 생기기
전에는 `REVIEWED`나 `APPROVED`를 기록하지 않는다. 작성자의 self-approval은 허용하지 않는다.

## 9. 검증 설계

`ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py`가 최소 다음을 검증한다.

1. Phase 0에서 승인된 최종 Schema Set graph 전체가 `load_dataset()`을 통과한다.
2. `DEV=60`, 다섯 주제별 12개, 여섯 표현 유형별 10개가 exact-match한다.
3. 모든 Case가 `RETRIEVAL`, `SYNTHETIC`, `DRAFT`이며 required Evidence를 하나 이상 가진다.
4. query가 `SYNTHETIC_QUERY_*` token이 아니라 한국어 자연문장이다.
5. 가상 제품 allowlist 밖의 실제 제품명과 개인정보 sentinel이 없다.
6. 네 Leakage 축이 비어 있지 않고 Case별 배분 및 partition-neutral canonical identity 규칙을 만족한다.
7. `transform_origin`은 정확히 20개이며 각 group은 3개 Case를 가진다.
8. 모든 Gold ref가 Evidence mapping에 존재하고 required가 relevant의 부분집합이다.
9. `slice_ids`와 Policy scope가 전체·Topic·Expression 배분 및 최소 Group 수와 일치한다.
10. 합성 Index가 Gold 20개와 네 유형별 hard negative 80개를 exact-match한다.
11. 모든 negative가 target transform origin, negative type, non-relevance와 고유 ID/hash에 결속된다.
12. Case input hash, resource hash, graph manifest hash와 protected receipt가 canonical 값과 일치한다.
13. HOLDOUT Case 또는 HOLDOUT 질문 resource가 일반 저장소 경로에 없다.
14. `status.json`이 strict local model을 통과하고 생성된 `report.md` byte가 commit된 projection과 일치한다.

새 최종 Schema Set 검증과 별개로 기존 Schema Set `1.2.0` fixture·export·Loader 테스트를 변경 없이 다시 실행해
하위 호환성을 확인한다. 새 member를 `1.2.0` registry에 역으로 추가하지 않는다.

Schema·privacy·hash·cross-partition leakage 위반은 기존 Loader의 안정 오류 code를 검증한다. 수량·Topic·표현
유형·hard-negative cardinality처럼 `#273` Dataset에만 적용되는 규칙은 전역 Loader 계약을 확장하지 않고
fixture test의 assertion으로 고정한다. 향후 여러 Dataset이 소비하는 공통 규칙이 되면 별도 Decision과 안정
오류 code를 추가한다. 어느 실패 경로도 질문 원문을 오류에 노출하지 않는다.

## 10. 실제 Retrieval 연결 경계

실제 평가는 `#178`의 concrete `EvidenceSearchPort`와 rerank Adapter가 versioned Knowledge Evidence Index에
연결된 뒤에만 수행한다. 실제 Evaluation Adapter ID는 `knowledge-evidence-retrieval.actual.v1`로 고정한다.
`ActualRetrievalModelConfig`는 기존 execution config의 versioned `model_config` payload를 다음 값에 결속한다.

- query fingerprint algorithm과 평가용 key version
- filter, Knowledge Index, retrieval, lexical, dense, rerank configuration ref
- `rag-natural-language-retrieval-dev-v1.index-build-receipt.json`의 ID·version·hash
- top-k `5`와 final rerank selection contract
- 실제 Adapter artifact ID·version·hash

CLI registry는 replay Adapter와 actual Adapter를 ID exact-match로 분기한다. unknown Adapter를 non-replay로
간주하지 않는다. actual Adapter가 registry에 없으면 Case 결과는 `NOT_IMPLEMENTED/null`이다. Adapter가
구현됐지만 필수 Gold review, Index Receipt 또는 실행 승인이 없어 실행하지 않은 상태만
`NOT_EVALUATED/null`이다.

실행 preflight는 Suite의 `adapter_id`, execution config의 `retrieval_variant.model_config.adapter_id`, registry가
resolve한 Adapter의 self-declared `adapter_id`를 모두
`knowledge-evidence-retrieval.actual.v1`과 exact-match한다. 세 값 중 하나라도 다르거나 Adapter가 자신의
artifact ID·version·hash를 제시하지 못하면 실행하지 않고 `INVALID/null`과 안정 mismatch code를 기록한다.
unknown ID, Suite/config ID 불일치, registry 반환 Adapter ID 불일치를 각각 negative test로 고정한다.

Evaluation Adapter는 다음 변환만 소유한다.

1. DEV Case query를 transient `SensitiveText`와 승인된 query fingerprint로 만든다.
2. Case와 고정된 Index·filter·retrieval·lexical·dense·rerank config ref를 Kernel request에 결속한다.
3. 실제 Port가 반환한 최종 rerank selection을 순서대로 검증한다.
4. 아래 Gold–runtime bridge를 exact-match해 Case별 ranked Evidence ID로 변환한다.
5. 최종 top-5를 `retrieved_evidence_ids`와 `selected_evidence_ids`에 같은 순서로 기록한다.
6. 기존 Retrieval Metric Kernel은 `retrieved_evidence_ids`의 top-5를 소비한다.
7. Kernel과 Adapter 상태를 Evaluation 실행 상태와 안정 failure code로 변환한다.
8. raw query, Evidence content, Provider body를 Run artifact나 일반 로그에 저장하지 않는다.

### Gold–runtime Index bridge

합성 JSON 파일 전체 hash와 #178의 개별 chunk content hash는 의미가 다르므로 서로 대신 사용하지 않는다.
단계 C의 결정적 Index build receipt는 각 record에 다음을 함께 결속한다.

```text
evidence_ref_id
→ Evidence Mapping stable_key
→ #178 evidence_key
→ knowledge_chunk_ref
→ resource locator
→ per-chunk content_sha256
→ source_snapshot_ref
→ evidence_index_ref
→ canonicalization_spec_version
→ index build config·artifact ref
```

Evaluation Evidence Mapping의 `fixture_record_ref.sha256`과 entry `content_sha256`은 기존 Loader 계약대로 합성
resource 파일 byte hash를 유지한다. 개별 chunk hash는 Index resource record와 build receipt가 소유한다.
실제 선택 결과는 `evidence_key`, `knowledge_chunk_ref`, locator, source version, per-chunk content hash,
Source Snapshot과 Knowledge Index ref를 receipt와 모두 exact-match해야 한다. 하나라도 다르면 miss로
채점하지 않고 Case를 `INVALID/null`로 닫는다.

### Kernel 상태 매핑

| #178 Kernel 결과 | Evaluation 결과 | ranked IDs |
| --- | --- | --- |
| `SUCCEEDED/CANDIDATES_RERANKED` | `COMPLETED/N/A` | 검증된 final rerank top-5 |
| `SUCCEEDED/NO_HITS` | `COMPLETED/N/A` | 빈 배열 |
| request·binding·result·receipt mismatch | `INVALID/null` | 빈 배열 |
| verifier·search·rerank 실제 dependency 오류 | `ERROR/null` | 빈 배열 |

원래 exception message는 보존하지 않고 안정 failure code만 기록한다.

Kernel fake Port, replay JSON 또는 수동 순위는 이 경로에서 금지한다. 실제 Adapter가 없으면 실행 상태를
`NOT_IMPLEMENTED`, 판정을 `null`로 유지하고 `BLOCKED_BY_RAG_14_ADAPTER`를 기록한다. Adapter 실행 전의
추적 validation report는 존재하지 않는 machine Run 상태를 만들지 않고 `Run Artifact: NOT_CREATED`와 차단
code만 기록한다.

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

Reporter의 Markdown `Data Source` projection은 replay를 `SYNTHETIC_REPLAY_DEV`, 위 3-way preflight와 실제
Adapter·Index provenance 검증을 통과한 Adapter를 `ACTUAL_RETRIEVAL_DEV`, 그 밖의 허용된 generic Adapter를
`ADAPTER_EXECUTION_DEV`로 표시한다. Reporter가 임의 문자열을 다시 판별하지 않도록 config/registry
preflight가 만든 typed verified-source classification을 전달한다. unknown, unresolved, ID mismatch Adapter는
actual로 표시하지 않는다. replay·verified actual·generic·unknown/mismatch 분기를 각각 Reporter와 통합
negative test로 고정한다. 모든 Data Source label은 실행 성공을 뜻하지 않으므로 execution status를 항상 함께
표시한다. 향후 이 분류를 JSON Artifact의 새 필드로 승격할 때만 Evaluation Result 계약 변경을 요구한다.

`report.md`는 JSON의 비정본 projection이라 Metric semantic hash의 입력은 아니지만 발행된
`result-content-manifest.json`의 immutable member다. 발행 뒤 수정하면 기계 판정 값 자체는 바뀌지 않아도
Bundle byte-integrity 검증은 실패한다.

저장소에 추적하는 `docs/validation/rag/issue-273/status.json`은 비민감 진행 상태의 기계 입력이고,
`report.md`는 이 JSON과 검증된 Run Artifact의 allowlist 요약만 소비하는 결정적 projection이다. 생성기는 raw
query, Evidence body, Provider payload 또는 protected path를 입력으로 받지 않는다. 테스트는 strict local
status model, canonical hash와 report exact-byte 재생성을 검증한다. 수동 편집으로 상태나 hash를 바꾸지 않는다.
`report.md`는 다음 항목만 담는다.

- Dataset identity와 Case/주제/표현/독립 Group 수
- Dataset·Gold·Index·config·Git revision hash
- 실행한 검증 명령과 결과
- Gold review와 HOLDOUT Freeze 상태
- actual Adapter 실행 여부와 차단 코드
- 실제 Run이 생긴 뒤의 Run ID·semantic hash·비민감 Metric 요약
- DEV 결과는 Release PASS가 아니라는 명시적 해석 제한
- `#158` replay와의 paired comparison 불가 사유

`status.json`은 최소한 `schema_version`, `issue`, `dataset_ref`, Case/Topic/Expression/Group counts,
`schema_set_ref`, `gold_review_status`, `holdout_freeze_status`, `adapter_status`, `blocking_codes`, 실행 명령별
exit status, nullable `actual_run_ref`와 `updated_at`을 가진다. 실행 전 `actual_run_ref=null`이며 Metric 필드는
존재하지 않는다. 실제 Run 뒤에는 검증된 `run_id`, result content manifest hash와 비민감 Metric summary만
복사한다.

Adapter 실행 전에는 Metric 값을 `0`으로 채우지 않는다. Adapter 자체가 없으면 machine 실행을 만들지 않고
validation report에 `BLOCKED_BY_RAG_14_ADAPTER`만 기록한다. 구현된 Adapter가 있으나 필수 입력 검증 전이면
실행 상태를 `NOT_EVALUATED/null`로 기록한다.

### `#158` replay와의 비교

`#158`의 `rag-retrieval-dev@1.0.0`과 이번 Dataset은 Case·Gold·corpus가 다르므로 기존 paired comparison
builder에 함께 입력할 수 없다. validation report에는 pipeline provenance를 정성 비교하되 Metric delta는
만들지 않고 `NOT_COMPARABLE_DIFFERENT_DATASET`을 기록한다. 수치 비교가 필요하면 새 자연어 Dataset과 같은
Case·Gold·Index를 사용하는 lexical baseline과 hybrid+rerank candidate를 각각 실제 Adapter로 실행한다.

## 12. HOLDOUT 경계와 study binding

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

DEV와 HOLDOUT은 권한을 분리하기 위해 서로 다른 Dataset으로 둔다.

| 역할 | Dataset identity | 저장 경계 |
| --- | --- | --- |
| 공개 DEV | `rag-natural-language-retrieval-dev@1.0.0` | repository `evals/` |
| 보호 HOLDOUT | `rag-natural-language-retrieval-holdout@1.0.0` | 승인된 protected evaluation root |

두 Dataset은 질문 본문을 포함하지 않는 `rag-natural-language-retrieval-study@1.0.0` split receipt로 결속한다.
Receipt는 두 Dataset hash, 공통 Gold schema, 공통 study-wide Knowledge Index·config ref와 승인 주체를
기록한다.
현재 Loader는 Dataset 내부 leakage만 확인하므로 별도 cross-dataset validator가 보호 환경에서 DEV/HOLDOUT
전체 Case graph를 함께 읽고 다음 교집합이 모두 0인지 검사한다.

- 네 Leakage 축의 partition-neutral canonical HMAC
- query를 NFC·공백·문장부호 규칙으로 정규화한 exact-query fingerprint
- reserved virtual medication·strength token을 typed placeholder로 치환한 simple-substitution fingerprint
- authoring transform specification과 base-intent seed를 결속한 content-derived transform fingerprint

작성자가 서로 다른 Leakage ID를 부여해도 content fingerprint가 같으면 Freeze를 거부한다. HOLDOUT 질문
본문과 cross-dataset validator가 파생한 모든 fingerprint·HMAC 값은 protected root 밖으로 내보내지 않고 split
receipt에는 Dataset hash, fingerprint algorithm version, 축별 비교 건수와 intersection count `0`만 기록한다.
DEV/HOLDOUT 점수는 별도로 보고하며 승인된 estimator 없이 100개 aggregate score를 만들지 않는다.

### Phase 0 — 새 provenance 계약과 Schema Set 승인 경계

Index build receipt와 study split receipt는 기존 `ProtectedArtifactReceipt`로 대신하지 않는다. Dataset graph를
생성·검토·Freeze하기 전에 별도 Decision과 Evaluation Contract Freeze에서 다음 machine contract를 승인한다.

- `rag-eval.index-build-receipt@1.0.0`
- `rag-eval.study-split-receipt@1.0.0`
- `rag-eval.authoring-identity-manifest@1.0.0`

계약 문서는 `docs/contracts/targets/post-mvp-1/`와 계약 index에 등록돼 있으며, 후보
[`rag-eval.schema-set@1.3.0`](../../governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md),
SHA-256 `e9843e190fbfabc6305d709e04ea296aefd107e66739882471fa3aedee08092f`에서 schema member·canonical
hash·registry를 고정한다. validator는 unknown key, ID/version/hash 불일치, per-record bridge,
canonicalization version, HMAC algorithm/key version, intersection count와 승인 역할을 fail-closed 검증한다.
이 Schema Set은 `Candidate · Review Required`이며 책임 리뷰어의 실제 Pull Request review event 전에는 승인된
계약이나 실제 Run 입력으로 사용하지 않는다. 기존 `1.2.0` member bytes는 수정하지 않는다.

Phase A의 Case 내용은 `1.2.0` 구조와 호환되게 초안 작성할 수 있지만, 최종 Dataset Manifest·Policy·Suite·
Evidence Mapping·protected receipt·authoring identity sidecar의 version/hash 결속과 `FROZEN` 전이는 Phase 0의
최종 Schema Set으로 한 번만 수행한다. 이미 Freeze한 `1.2.0` graph를 다음 Schema Set으로 조용히 바꾸지
않으며, 예외적으로 선행 Freeze가 발생했다면 Dataset/Policy version bump, 전체 hash 재생성, Gold 재검토와
재승인을 요구한다.

현재 CLI는 `run-dev`와 repository-root DEV config만 지원한다. protected root loader,
`run-protected-holdout`, authorization receipt, 결과 저장 root와 접근 audit는 **#273 전용 protected Retrieval
실행 후속 Issue**가 소유한다. #157에서는 공통 Runner·Reporter·publisher component만 재사용하고, #157의
Answer·Citation·Safety까지 포함한 공통 HOLDOUT gate를 #273 Close 조건으로 상속하지 않는다. 전용 후속
Issue는 Phase 0 계약 승인 뒤, HOLDOUT 질문 작성 전에 생성해 구현 담당자와 Product·Safety·Privacy 책임
리뷰어를 명시한다. 해당 경계가 구현·승인되지 않으면 `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`로 단계 D를
실행하지 않고 #273을 Open으로 유지한다.

## 13. 실패·안전 처리

- Dataset graph 불일치: 실행 전 `INVALID/null`
- Gold 미검토 또는 필수 Receipt 미승인: `NOT_EVALUATED/null`
- Adapter 미구현·registry 미등록: `NOT_IMPLEMENTED/null`, `BLOCKED_BY_RAG_14_ADAPTER`
- Index·config·Git revision 불일치: `INVALID/null`
- Gold–runtime bridge 불일치: `INVALID/null`
- Case Adapter 오류: 해당 Case `ERROR/null`, 비민감 reason code만 기록
- 최소 Case·독립 Group 부족: `COMPLETED/INCONCLUSIVE`
- 승인 threshold 부재: Metric은 diagnostic으로 기록하되 품질 PASS 금지
- 개인정보·credential·실제 의료 데이터 가능성: 작성 또는 실행 즉시 중단

실패 report와 `failures.jsonl`에는 Case ID, stage, 안정 reason code만 기록한다. query 원문, Evidence content,
exception message, SQL parameter, Provider payload는 기록하지 않는다.

## 14. 단계별 전달 계획

### Phase 0 — provenance 계약 확장

- 세 신규 provenance 계약의 Decision, contract index, schema export·registry·Loader 테스트를 별도 계약 PR로 승인
- 최종 Schema Set ID·version·hash와 #273 graph가 사용할 member version 확정
- 이 단계는 질문 본문이나 HOLDOUT을 생성하지 않음

### 단계 A — DEV authoring

- Phase 0에서 승인한 Schema Set으로 DEV 60개, 합성 Evidence Index, Gold mapping과 authoring graph 작성
- exact distribution·privacy·leakage·hash 검증 추가
- strict `status.json`에서 `docs/validation/rag/issue-273/report.md`를 생성하고 exact-byte 검증

### 단계 B — Gold review

- 담당 리뷰어의 실제 검토 evidence 확보
- `dataset.status=DRAFT`를 유지하면서 `review_provenance.team_gold_status`를 실제 event에 따라
  `DRAFT → REVIEWED → APPROVED`로 전이
- Dataset Freeze가 승인되면 Phase 0의 최종 Schema Set으로 한 번만 `dataset.status=FROZEN`과 `frozen_at` 기록
- 승인 event 없이 상태를 미리 변경하지 않음

### 단계 C — actual Adapter integration

- `#178` concrete Adapter와 versioned Index Receipt 확인
- `transform_origin` Metric signature, actual Adapter config·registry·CLI와 Gold–runtime bridge 구현
- Evaluation Adapter 연결 및 DEV 60개 실행
- 동일 입력 반복 실행의 semantic hash 또는 허용된 결정성 Receipt 비교
- 실제 Metric·failure 요약을 validation report에 갱신

### 단계 D — HOLDOUT preparation and run

- 별도 접근 통제 아래 40개 작성·검토·Freeze 및 study split receipt 생성
- #273 전용 protected Retrieval 실행 후속 Issue의 loader·실행 승인·접근 audit 확인
- 승인된 Retrieval-only Policy 이후 HOLDOUT 40개 최초 실행
- 단계 C의 DEV Run과 Dataset·Gold·Index·Adapter·config가 동일한 study binding이면 그 Run을 결속하고, 하나라도
  달라졌으면 고정된 최종 artifact로 DEV 60개를 다시 실행
- DEV 60개와 HOLDOUT 40개의 case·metric·failure artifact, Run ID, semantic hash를 partition별로 보존
- DEV와 HOLDOUT 점수는 분리하고, 승인된 estimator가 없는 100개 aggregate score를 만들지 않으며 최종 Release
  입력은 후속 Gate에 전달

### 의존성 비전이 규칙

| 단계 | 필요한 의존성 | 명시적 비의존성 |
| --- | --- | --- |
| Phase 0 | Evaluation 계약 Decision과 지정 계약 리뷰 | `#159`, `#160`, `#161`, `#278` |
| A/B | Phase 0 Schema Set, `#273` Gold/Dataset review | `#159`, `#160`, `#161`, `#278` |
| C | `#178` actual Adapter·Index, Retrieval Metric algorithm support | `#159`, `#160`, `#161`, `#278` |
| D | #273 전용 protected Retrieval 실행 Issue, 승인된 Retrieval-only Policy·Freeze | `#159`, `#160`, `#161`, `#278` |

`#159`·`#160`·`#161`은 #278와 최종 Production Release Gate의 의존성이며 Retrieval-only #273의 단계나
Close 조건으로 전이시키지 않는다.

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

- DEV 60개와 Gold authoring graph가 Phase 0에서 승인한 최종 Schema Set으로 저장된다.
- 주제·표현 유형·Leakage·privacy·hash 검증이 통과한다.
- 저장소 validation report가 실제 실행 여부와 차단 상태를 정확히 표현한다.
- HOLDOUT 본문과 실제 Retrieval Metric은 생성되지 않는다.

Issue `#273` 전체 Close에는 추가로 다음이 필요하다.

- HOLDOUT 40개의 승인된 Freeze·접근 통제 증빙
- 동일 study binding 아래 DEV 60개와 HOLDOUT 40개 모두 query replay가 아닌 `#178` 실제 Adapter로 실행
- 각 partition의 Case 수가 각각 60/40임을 증명하는 Dataset·Gold·Index·config·Git revision 결속
- DEV/HOLDOUT 각각의 Run ID·semantic hash·Metric·case·failure artifact와 study split receipt
- 동일 입력 반복 실행의 결정성 또는 승인된 허용 범위 증빙
- 담당 리뷰어의 threshold·HOLDOUT 사용 승인 기록
- #273 전용 protected Retrieval 실행 후속 Issue의 권한·audit·실행 증빙

이 평가만으로 임상적 유효성, Answer 안전성 또는 Production 공개 가능성을 주장하지 않는다.
