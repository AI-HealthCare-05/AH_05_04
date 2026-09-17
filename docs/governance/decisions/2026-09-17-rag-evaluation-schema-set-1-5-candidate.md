# Product Decision Candidate: RAG Evaluation Schema Set 1.5

| 항목 | 값 |
| --- | --- |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-17 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 (Schema Set Candidate / Target) | 권가빈 (`@hazelnutflavoured`) — PM / 제품·Safety·평가 계약 승인 (PR #764 책임 리뷰) |
| DEV 구현 리뷰 (Issue #159) | 김지혜 (`@Jye-rookie`) — DEV kernel 구현 리뷰 / 권가빈 (`@hazelnutflavoured`) — Contract 승인 |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 적용 범위 | Post-MVP-1 Track F Answer Quality human judgment / 3-pair comparison input contract candidate |

## 후보 결정

`rag-eval.schema-set@1.5.0`을 #159 Answer Quality human judgment·approval 및 3-pair comparison manifest
입력에 필요한 최소 후보 Schema Set으로 제안한다. 이 후보의 승인 전환에는 책임 리뷰어
권가빈 (`@hazelnutflavoured`)의 실제 Pull Request review event가 필요하며, schema와 문서의 존재만으로
그 event를 대신할 수 없다.

| Immutable field | 값 |
| --- | --- |
| Schema Set ID | `rag-eval.schema-set` |
| Schema Set version | `1.5.0` |
| Schema Set SHA-256 | `cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8` |
| Canonical member root | `evals/schemas/1.5.0/` |
| Member count | `26` |

Schema Set hash는 member별 `{schema_id, schema_version, schema_sha256}`를 정렬한 canonical JSON의
SHA-256이다. Set version과 member version은 독립적으로 검증한다.

## Member versioning

Schema Set `1.4.0`의 23개 member와 각 canonical bytes를 그대로 재사용하고 다음 세 member를
`1.0.0`으로 추가한다.

- `rag-eval.answer-human-judgment@1.0.0`
- `rag-eval.answer-human-judgment-approval@1.0.0`
- `rag-eval.answer-comparison-set-manifest@1.0.0`

따라서 전체 member는 경로와 schema ID가 각각 고유한 26개다. 기본 exporter version은 계속 `1.0.0`이며,
기존 `evals/schemas/1.0.0/`부터 `1.4.0/`까지의 bytes와 hash를 변경하지 않는다.

## Input 계약

### Human Judgment & Approval
Answer human judgment는 completed `END_TO_END_RAG` Case의 Run·Answer variant·Dataset·Rubric·Approval에
결속하며 Claim별 correctness label(`CORRECT | INCORRECT`)과 Case relevance label(`RELEVANT | IRRELEVANT`)을
보존한다. 질문·답변·Claim·Judge reasoning 원문은 저장하지 않는다. Approval artifact는 `approval_status=APPROVED`,
승인 actor/time 및 judgment artifact SHA-256 결속을 엄격히 강제한다.

### Answer Comparison Set Manifest
비교 manifest는 동일 Case 집합과 통제 변수 통제 하에 정확히 세 pair(`ANS-BASE -> ANS-RAG`, `ANS-RAG -> ANS-FINAL`,
`ANS-BASE -> ANS-FINAL`)만 허용하며, 각 pair별 `allowed_delta_keys` exact set을 검증한다.
`allowed_delta_keys`는 pair별 exact set이며 배열의 직렬화 순서는 의미를 갖지 않는다.
Draft 2020-12 schema에서도 `items.enum`, `uniqueItems: true`, `minItems/maxItems`로 pair별 키 집합을 fail-closed한다.
Relative path traversal(`..`, absolute path)을 차단하고 manifest self-hash를 검증한다.

## 적용 경계

이 후보는 세 evaluation schema model, canonical JSON Schema export, registry member와 schema-set hash만 소유한다.
다음은 포함하지 않는다.

- #159 human judgment loader/consumer
- `ANSWER_CORRECTNESS` 및 `RELEVANCE` metric scorer
- Answer pair comparison builder 또는 comparison-set manifest builder
- Runtime adapter·Provider·DB·network 연결
- frozen HOLDOUT 또는 SAFETY_REGRESSION 관찰·실행
- Baseline Freeze, active threshold, Release `PASS | FAIL`, `PUBLIC_TRACK_F`

책임 리뷰어의 실제 승인 전에는 Schema Set `1.5.0`을 Approved 입력으로 취급하거나 #159 metric
구현 선행조건이 완료됐다고 기록하지 않는다.
