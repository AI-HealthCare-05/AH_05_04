# RAG Evaluation Runner·Reporter 설계

| 항목 | 내용 |
| --- | --- |
| 관련 작업 | [Issue #157 RAG-EVAL-002 Evaluation Runner·Reporter 구현](https://github.com/AI-HealthCare-05/AH_05_04/issues/157) |
| 구현 담당 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — 평가 상태·결과 표현·승인 분리 |
| 교차 리뷰 | 송은영 (`@phina-io`) — Artifact 저장·DB 경계 |
| 기준 브랜치 | `feat/157-rag-evaluation-runner-reporter` |
| 문서 상태 | 설계 승인·구현 계획 완료 |
| 완료 의미 | 합성 DEV Runner·Reporter 기반 구현. HOLDOUT 실행·Baseline Freeze·Release 승인이 아님 |

## 1. 목적

이 작업은 versioned Evaluation Profile·Dataset Manifest·Partition·Variant를 검증하고 합성 DEV Case를
결정적인 순서로 실행하는 공통 Runner와, 기계 정본 JSON Artifact에서 비정본 Markdown을 생성하는
Reporter를 구현한다. 결과는 파일 전용 로컬·CI Artifact로 보존하며 환자 API, Application 결과, DB에
저장하지 않는다.

이번 구현은 #214에서 동결한 HOLDOUT·SAFETY_REGRESSION Dataset을 실행하기 위한 승인이 아니다. #158~#161
Metric의 DEV 검증, Runtime Source/Rule Bundle 고정, Comparison/Evaluation Policy 승인이 완료되기 전에는
HOLDOUT·SAFETY_REGRESSION Manifest의 Case·Gold·Evidence·Rubric을 load·execute·observe하지 않는다.

## 2. 권위와 입력 계약

설계는 다음 순서로 해석한다.

1. 저장소의 현재 코드·Schema·Loader·자동 테스트
2. `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
3. 외부 Authority Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`
4. 외부 정본 `evaluation-plan.md@1.35`, SHA-256
   `526f83dedc05a777c0963bfa10bb8bd8ebd940ab3eb12523f4c8fa15447e542f`
5. 외부 운영 보조 문서와 Roadmap

외부 `evaluation-minimum-operating-contract.md`, `rag-evaluation-plan.md`, `evaluation-system-roadmap.md`는
구현 이해를 돕는 비정본 자료이며 저장소 계약이나 정본 Evaluation Plan을 덮어쓰지 않는다.

Runner가 확인하거나 소비하는 현재 immutable 입력은 다음과 같다.

- 후속 HOLDOUT 실행의 승인된 선행 계약: Evaluation Schema Set `rag-eval.schema-set@1.2.0`, SHA-256
  `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06`
- 이번 DEV 실행이 실제로 load하는 authoring/policy 계약과 Dataset: `rag-dev-foundation@1.0.0`
- DEV Profile `rag-dev-foundation-profile@1.0.0`
- DEV Comparison Policy `rag-dev-foundation-comparison@1.0.0`
- DEV Evaluation Policy `rag-dev-foundation-policy@1.0.0`
- DEV Suite `rag-dev-foundation-validation-suite@1.0.0`
- Artifact Schema Set `rag-eval.schema-set@1.0.0`

Schema Set 1.2와 #214 Freeze는 #157 착수 가능성을 증명하는 선행 계약이지만 이번 `run-dev`의 payload가
아니다. `run-dev`는 현재 DEV Foundation의 member Schema 1.0만 load한다. Schema Set 1.2의 HOLDOUT payload를
DEV 실행에 섞거나 1.2를 1.0으로 변환하지 않는다.

이번 작업은 위 ID, version, hash, Schema 필드, enum, nullability를 변경하지 않는다. 변경 필요성이 발견되면
Runner 구현을 중단하고 새 Decision 또는 Contract Freeze version으로 분리한다.

## 3. 구현 범위

### 3.1 포함

- 기존 Evaluation CLI에 `run-dev` 명령 추가
- versioned DEV execution request loader와 resolved configuration hash 계산
- Manifest-only partition preflight와 DEV 전용 실행 경계
- 기존 `load_dataset()`을 통한 전체 Schema·hash graph·privacy·provenance 재검증
- Experiment Type별 Task Type 선택
- Case ID 기준 결정적 실행 순서
- Case별 Adapter 실행과 오류 격리
- 실행·판정 상태 분리와 부모 차단 상태 집계
- 승인된 Artifact Schema를 사용하는 JSON·JSONL 결과 생성
- 결과 파일의 canonical serialization과 content manifest 생성
- JSON 결과만 읽는 비민감 Markdown Projection
- 디렉터리 단위 no-clobber 원자적 발행
- 동일 의미 입력 2회 실행의 semantic content hash 비교
- HOLDOUT·SAFETY_REGRESSION 사전 차단 회귀 테스트

### 3.2 제외

- #214의 동결 Dataset·Case·Gold·Evidence Mapping·Rubric·provenance·hash graph 수정
- Retrieval·Answer·Citation·Safety Metric 계산
- 실제 RAG Runtime, Provider, Model, Judge 호출
- 실제 HOLDOUT·SAFETY_REGRESSION load·execute·result access
- Baseline Freeze 실행과 `baseline-freeze-receipt.json` 생성
- Baseline/Candidate 우열 판정과 Release Gate 활성화
- Comparison/Evaluation Policy Threshold 또는 승인 상태 변경
- 환자 API·Application 저장·DB schema·migration
- 실제 환자정보·Provider 원문·credential·Judge reasoning 저장
- `PUBLIC_TRACK_F` 또는 Production 공개 상태 변경

## 4. 단계 분리

Issue #157의 전체 수명주기는 두 단계로 분리한다.

### 4.1 이번 PR — DEV Runner·Reporter

합성 DEV Dataset으로 입력 검증, Adapter 호출, 상태 집계, Artifact 생성, 원자적 발행, Markdown Projection과
결정성을 검증한다. 실제 Adapter가 등록되지 않은 실행은 `NOT_IMPLEMENTED/null`을 정직하게 기록한다.

### 4.2 후속 단계 — 승인된 Baseline Freeze

#158~#161 Metric의 DEV 검증과 Runtime Source/Rule Bundle 고정, Comparison/Evaluation Policy 승인이 모두
확인된 뒤에만 HOLDOUT 전용 실행 경계를 연다. 이 단계는 명시적 Baseline/Candidate Run, 통제 설정,
반복 실행 결정성 증빙과 `baseline-freeze-receipt.json`을 소유한다. 이번 PR은 해당 Artifact를 생성하거나
승인 상태를 추정하지 않는다.

## 5. 구성요소

### 5.1 CLI

`ai_worker.tasks.evaluation.cli`은 기존 `validate` 명령을 그대로 유지하고 `run-dev`를 추가한다. CLI는
argument parsing, 허용된 결과 root, Manifest preflight, exit code와 안전한 오류 code 출력만 담당한다.
Runner 내부 상태를 문자열 메시지로 추정하지 않는다.

`run-dev`는 versioned DEV execution request, Run ID, 결과 root와 실행 주체를 명시적으로 받는다. Dataset
Manifest를 별도 argument로 중복 입력하지 않고 request의 검증된 참조에서만 해석한다. 테스트에서는 clock과
Adapter registry를 dependency로 주입한다. Production 경로는 저장소의
`evals/results/<run_id>/`만 허용하고 테스트는 임시 allowed root를 주입한다.

#### 5.1.1 DEV execution request

`evals/configs/dev-foundation-v1.execution.json` 같은 저장소 관례의 파일은 CLI argument의 흩어진 문자열로 provenance를
조립하지 않기 위한 Runner 전용 입력이다. `DevExecutionRequest` Pydantic model은 `extra="forbid"`로 다음 필드를
검증한다.

- `config_id`, `config_version`, `experiment_id`, `experiment_type`, `variant_id`, `evaluated_partitions=["DEV"]`
- `environment="LOCAL" | "CI"`
- `dataset_manifest_path`, `profile_path`, `comparison_policy_path`, `evaluation_policy_path`, `suite_path`
- `upstream_contract_manifest_hash`
- `retrieval_variant`, `answer_variant` — 적용되지 않으면 명시적 `null`; 적용되면 ID, version, kind,
  `model_config`, `prompt_version`과 JSON-compatible parameter map을 가진 내부 value object
- `seed`
- `retry_policy="NO_AUTOMATIC_RETRY"`, `max_attempts=1`

이 파일은 `run-dev` CLI와 Runner만 소비하는 저장소 내부 실행 요청이며 기존 `rag-eval.*` 공유 Schema나
교차 도메인 DTO가 아니다. 따라서 이번 PR에서 `evals/schemas/`, `docs/contracts/` 또는 동결 Artifact를
변경하지 않는다. 다른 도메인이 이 형식을 소비해야 하는 요구가 생기면 구현을 중단하고 별도 공유 계약으로
승격한다.

Loader는 저장소 root 기준 정규화 상대경로만 허용하고 symlink, root 이탈, 중복 key와 알려지지 않은 필드를
거부한다. 각 참조 파일을 기존 Loader로 검증한 뒤 execution request의 모든 의미 필드와 검증된 입력의 실제
canonical hash를 고정 순서 map으로 직렬화한다. 그 bytes의 SHA-256이
`resolved_evaluation_config_hash`이다. 사용자가 hash를 직접 입력하거나 Runner가 placeholder 값을 만들지 않는다.
Loader는 Profile, Comparison Policy, Evaluation Policy, Suite를 읽은 각 snapshot의 상대경로와 file SHA-256을
역할별 binding으로 보존한다. 실행 전 검증은 네 request path를 하나의 set으로 축약하지 않고 각 필드를 해당
역할의 실제 binding과 개별 비교한다. path가 다르면 `EVAL_MANIFEST_INVALID`, 같은 path의 hash가 다르면
`EVAL_HASH_MISMATCH`로 실패하므로 Case JSON 등 이미 graph에 존재하는 다른 리소스로 역할을 오배선할 수 없다.
각 non-null variant value object의 canonical bytes SHA-256을 해당
`retrieval_variant_manifest_hash`·`answer_variant_manifest_hash`로 사용한다. `RagEvaluationRun`에 존재하는
`experiment_id`, `experiment_type`, `variant_id`, 각 variant manifest hash, `upstream_contract_manifest_hash`,
`prompt_version`은 검증된 요청에서 복사하고 `model_config_hash`는 실제 `model_config` canonical bytes에서
계산한다. Runner code commit은 tracked config 안에 자기 자신의 commit SHA를 넣는 순환을 만들지 않고 실행
시점의 현재 commit을 읽어 resolved configuration hash preimage에 추가한다. 현재 commit을 확인할 수 없거나
working tree가 dirty이면 Production `run-dev`를 거부하며 테스트는 commit provider를 주입한다. `seed`와 Runner
code commit은 현재 Artifact Schema에 독립 필드가 없으므로 값을 새 필드로 꾸며내지 않고 resolved configuration
hash에만 결속한다. 두 variant가 동시에 존재할 때 서로 다른 model/prompt 설정을 허용해야 한다면 현재 단일
`model_config_hash`·`prompt_version` 계약으로 표현할 수 없으므로 실행 전 `INVALID/null`로 거부하고 별도 Schema
version 논의로 분리한다.

### 5.2 Manifest preflight

execution config 해석 시 Dataset Manifest bytes를 한 번 읽어 resolved input에 결속한다. preflight와
`load_dataset()`은 이 동일한 immutable snapshot을 소비하며 다음 조건을 확인한다.

- execution request가 요구하는 partition이 정확히 `DEV`
- Manifest가 선언한 DEV Case 수가 1개 이상
- `AUTHORING=0`, `HOLDOUT=0`, `SAFETY_REGRESSION=0`
- Manifest의 모든 `case_resources[].partition`이 정확히 `DEV`
- `data_classification=SYNTHETIC`

따라서 preflight와 Loader 사이에 Manifest 파일이 교체되어도 새 내용을 열어 child resource를 관찰하지 않는다.
공식 HOLDOUT Manifest처럼 DEV 외 partition을 선언한 입력은 `load_dataset()` 호출 전에 거부한다. preflight는
전체 Dataset 수용 경계가 아니며, 통과 뒤 기존 Loader가 child resource와 hash graph 전체를 다시 검증한다.
Manifest가 partition count를 거짓으로 표시한 비정상 입력은 Loader의 Case·Manifest 정합성 검사에서
`INVALID/null`로 실패한다. Dataset ID/version, 정확한 Case set과 Task Type, Profile·Policy·Suite 연결은
preflight에 중복 하드코딩하지 않고 기존 Loader와 Suite의 selector/hash 검증에 맡긴다. Dataset의 review status는
Artifact에 사실대로 보존하되, 합성 DEV 여부를 대신하는 접근 제어 조건으로 사용하지 않는다.

### 5.3 Runner

`runner.py`는 순수 orchestration과 상태 집계를 담당한다. Loader가 반환한 immutable model을 변경하지 않으며
Case를 `case_id`의 UTF-16BE lexical order로 실행한다. 한 Case의 Adapter 예외는 schema-valid
`CaseResult(ERROR/null)`와 비민감 `failure_codes`로 변환하고 다음 Case 실행을 계속한다.

Experiment Type과 Task Type은 다음처럼 고정한다.

| Experiment Type | 실행 Task Type |
| --- | --- |
| `KNOWLEDGE_RETRIEVAL` | `RETRIEVAL` |
| `ANSWER_GROUNDING_SAFETY` | `ANSWER_GROUNDING`, `ANSWER_QUALITY`, `SAFETY` |
| `END_TO_END_RAG` | `END_TO_END_RAG` |

Task Type의 정렬은 wire value의 UTF-16BE lexical order를 사용한다. 선택된 Experiment Type에 필요한 Case가
없거나 중복되면 실행 전 `INVALID/null`로 종료한다.

### 5.4 Adapter 경계

Adapter는 한 Case와 해석된 비민감 Evidence 입력을 받아 승인된 `CaseResult` model을 반환하는 인터페이스다.
Runner는 Provider SDK, Backend DI, DB session에 직접 의존하지 않는다.

이번 PR은 실제 RAG Adapter를 등록하지 않는다. 테스트는 다음 Fake Adapter를 주입한다.

- 성공 Adapter: Task별 schema-valid `COMPLETED/N/A` 합성 결과 반환
- 오류 Adapter: 지정 Case에서 내부 예외 발생
- 미구현 Adapter: `NOT_IMPLEMENTED/null` 결과 반환

성공 Fake의 `N/A`는 DEV infrastructure 검증이며 Metric 또는 Release PASS가 아니다. Gold 값을 actual 결과로
복사해 성공을 제조하지 않고 테스트가 독립적으로 제공한 합성 actual payload를 사용한다.

Artifact Schema 1.0은 실행 상태와 무관하게 일부 Task별 actual 필드를 요구한다. 미구현·오류 Case에는
collection을 빈 배열, invocation boolean을 `false`, nullable 필드를 `null`로 기록한다. Answer 계열의 필수
`answer_sha256`은 실제 Answer가 없다는 byte-level 사실을 나타내는 `SHA-256(empty bytes)`
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`로 기록한다. 이 값은 PASS, 생성 Answer,
Provider 호출 또는 Gold 복사를 의미하지 않으며 `execution_status != COMPLETED`, `decision_status=null`과 함께만
사용한다.

### 5.5 Manifest와 Artifact builder

`manifest.py`는 선택 Case set, artifact bytes와 hash를 계산한다.
serialization은 기존 `canonical_json_bytes()`를 재사용한다. JSONL은 각 record를 canonical JSON 한 줄로
직렬화하고 LF로 종료하며 빈 줄·주석·header record를 만들지 않는다.

각 `CaseResult.input_sha256`은 다음 map의 canonical JSON bytes를 SHA-256한 값으로 고정한다.

```text
case_id, task_type, partition, case_resource_sha256,
dataset_manifest_sha256, evidence_mapping_manifest_sha256,
critical_claim_rubric_hash, resolved_evaluation_config_hash
```

Case 파일 hash만 사용하거나 질문·Gold·Evidence 원문을 preimage에 직접 복사하지 않는다. 동일 Case라도 Variant,
config 또는 provenance graph가 바뀌면 `input_sha256`이 달라져야 한다.

### 5.6 Reporter

`reporter.py`는 검증된 Dataset·config·Case/Metric/Suite model에서 만든 immutable `ReportData`와 machine entry만
입력받아 `report.md`를 생성한다. content manifest hash가 아직 없는 미완성 `run.json`이나 Dataset 질문, 답변
원문, Provider body, 내부 reasoning을 입력받지 않고 다음 비민감 정보만 표시한다.

- Run ID, Experiment Type, Variant ID
- Dataset·Profile·Policy·Suite ID/version/hash
- 실행·판정 상태와 정렬된 blocking status
- Task Type별 Case 수와 안전한 failure code
- Reporter 자신과 content manifest를 제외한 기계 Artifact 상대경로와 hash
- `DEV validation only`, `Not a Release decision` 고지

Markdown은 판정 입력이 아니며 수정·삭제해도 JSON 기계 판정이 바뀌지 않는다.

## 6. 데이터 흐름

```text
run-dev arguments
  → DEV execution request와 Variant value object 검증·resolved config hash 계산
  → safe path 및 Manifest-only partition preflight
  → load_dataset() 전체 graph 검증
  → Profile·Policy·Suite와 요청 Experiment 정합성 검증
  → Case set 결정·정렬
  → Case별 Adapter 실행과 Failure 격리
  → Case·Suite·Metric·Run 상태 집계
  → run.json을 제외한 schema-valid JSON/JSONL payload와 machine entry 목록 생성
  → machine entry 목록 기반 report.md 생성
  → report.md를 포함하고 run.json과 자기 자신을 제외한 result-content-manifest.json 생성
  → content manifest hash로 schema-valid run.json 확정
  → 전체 Artifact 재검증과 privacy 검사
  → private staging directory fsync
  → evals/results/<run_id>/ no-clobber atomic rename
```

## 7. Artifact 생성 규칙

Artifact Schema Set 1.0의 8개 Schema ID를 그대로 사용한다. 각 명령은 자신이 유효하게 생성할 수 있는
Artifact만 발행한다.

### 7.1 단일 DEV Run

- `run.json`
- `cases.jsonl`
- `metrics.json`
- `suite-results.json`
- `failures.jsonl`
- `result-content-manifest.json`
- `report.md`

`metrics.json`은 Policy가 선언한 Scope를 보존하되 구현되지 않은 Metric을 `NOT_IMPLEMENTED/null`과 계산값
`null`로 기록한다. DEV Policy에서 이 Metric은 `required=false`이므로 성공 Fake Adapter가 모든 선택 Case를
`COMPLETED/N/A`로 끝낸 infrastructure Run은 `COMPLETED/N/A`가 될 수 있다. Adapter가 없거나 Required child가
미완료이면 Run은 해당 blocking execution status와 `decision_status=null`을 기록한다.

### 7.2 비교·Gate 단계

`comparison.json`은 schema-valid Baseline Run과 Candidate Run 두 개가 모두 제공된 별도 비교 단계에서만
생성한다. `gate.json`은 Required Metric·Suite·Contract Receipt 입력이 모두 식별된 별도 집계 단계에서만
생성한다. 이번 PR은 이 두 단계를 위한 내부 경계를 설계하되 CLI로 활성화하거나 파일을 생성하지 않는다.

가짜 Run ID, 영(0) hash, 빈 통제 변수, 빈 Scope Comparison을 만들어 파일 수를 맞추지 않는다. Artifact가
존재하지 않는 사실 자체를 PASS·FAIL·INCONCLUSIVE로 변환하지 않는다.

`result-content-manifest.json`은 실제 생성된 payload 중 `run.json`과 자기 자신을 제외한 허용 파일만
`relative_path` UTF-16BE lexical order로 기록한다. Reporter에는 `report.md`와 content manifest를 제외한
machine entry 목록의 읽기 전용 view만 전달한다. Reporter는 자신의 hash를 표시하거나 content manifest를
다시 hash하거나 파일 시스템을 재탐색하지 않으므로 self-reference와 hash cycle이 생기지 않는다.

`failures.jsonl`의 `FailureRecord.expected_summary/actual_summary`는 기존 Schema enum으로 정확히 표현 가능한
평가 분석 실패만 기록하며 해당 실패가 없으면 빈 파일이다. Adapter 예외를 기존 의료·근거 실패 enum 중 하나로
오표현하지 않는다. 기술 실행 오류의 정본은 `cases.jsonl`의 `execution_status=ERROR`, `decision_status=null`,
안전한 `failure_codes`이며 Suite와 Run이 이를 blocking status로 집계한다.

## 8. 상태와 실패 처리

실행 상태와 판정 상태는 독립 축이다.

| 상황 | execution status | decision status | 처리 |
| --- | --- | --- | --- |
| 입력 Schema·hash·partition 위반 | `INVALID` | `null` | Case 실행 전 거부 |
| Adapter 내부 오류 | `ERROR` | `null` | 해당 Case Failure 기록 후 계속 |
| Adapter 또는 Metric 미구현 | `NOT_IMPLEMENTED` | `null` | 미구현 상태 보존 |
| 승인·필수 입력 전 미실행 | `NOT_EVALUATED` | `null` | 실행하지 않은 상태 보존 |
| DEV infrastructure 합성 검증 완료 | `COMPLETED` | `N/A` | Release 근거 사용 금지 |

Required child 또는 선택된 Case에 미완료 상태가 있으면 부모 판정은 `null`이다. 비필수 Diagnostic Metric의
`NOT_IMPLEMENTED`만으로 성공한 DEV infrastructure Run을 실패나 Release PASS로 바꾸지 않는다. 부모 `execution_status`와
`blocking_execution_statuses[]`는 기존 Schema의 우선순위를 따른다.

```text
INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED
```

Case 예외의 원문, path, payload는 Artifact와 stderr에 쓰지 않는다. `cases.jsonl`에 allowlist의 안정된
Evaluation error code와 Case ID만 남긴다. 예상하지 못한 예외는 `EVAL_INTERNAL_ERROR`로 정규화한다.

Manifest-only preflight나 전체 Loader가 실패하면 `RagEvaluationRun`의 필수 provenance를 정직하게 채울 수
없으므로 최종 Run directory와 부분 Artifact를 만들지 않는다. CLI는 안전한 오류 code와 non-zero exit만
반환한다. 위 표의 `INVALID/null` Run Artifact는 필수 입력 graph 검증이 끝난 뒤 Experiment·Variant 정합성 같은
실행 전 조건에서 실패하여 필수 provenance를 모두 기록할 수 있을 때만 생성한다.

### 8.1 재시도 정책

이번 DEV Runner는 자동 재시도를 하지 않는다. execution request는
`retry_policy="NO_AUTOMATIC_RETRY"`, `max_attempts=1`이어야 하며 Runner는 선택된 `(case_id, task_type)`마다
Adapter를 정확히 한 번만 호출한다. timeout, provider 오류, 내부 예외도 같은 Run 안에서 재호출하지 않고
Failure와 blocking status로 보존한다.

운영자가 재실행할 때는 새 Run ID를 사용해야 하며 기존 결과를 덮어쓰지 않는다. Provider별 retry가 필요해지면
실패 분류, backoff, 최대 시도 수, 중복 비용과 결정성 영향이 포함된 승인된 Policy/version을 먼저 추가한다.
기존 Worker의 일반 retry 모듈을 Evaluation Runner에 암묵적으로 재사용하지 않는다.

## 9. 원자적 발행

preflight와 전체 입력 graph 검증을 통과한 결과만 허용 root 내부의 private staging directory에서 완성한다.

1. `<run_id>.lock`을 exclusive create한다.
2. mode `0700` staging directory를 만든다.
3. 각 파일을 mode `0600`으로 write하고 fsync한다.
4. run.json 이외의 Pydantic model, exported JSON Schema와 privacy boundary를 검증한다.
5. typed `ReportData`와 machine entry에서 Markdown을 만들고, 생성된 machine payload와 Markdown만 content manifest에 넣는다.
6. `COMPLETED` Run이면 `run.json.result_content_manifest_hash`를 확정한다. 미완료 Run은 Schema 계약대로
   해당 필드를 `null`로 유지하되 진단 보존을 위해 content manifest 파일 자체는 발행할 수 있다.
7. 최종 run.json을 Pydantic model, exported JSON Schema와 privacy boundary로 검증한다.
8. staging directory를 fsync한다.
9. 최종 `evals/results/<run_id>/`로 원자 rename한다.
10. parent directory를 fsync하고 lock을 정리한 뒤 다시 fsync한다.

기존 최종 디렉터리나 lock이 있으면 덮어쓰거나 삭제하지 않고 `EVAL_RESULT_PATH_CONFLICT`로 실패한다.
symlink component, root 이탈, Unicode 비정규화, `.`·`..`, cross-filesystem rename은 거부한다. 실패 시 새로 만든
staging만 정리하며 사용자 또는 다른 실행이 소유한 파일은 삭제하지 않는다. staging 생성 여부와 열린 fd는
별도로 추적한다. 따라서 `mkdir` 성공 직후 staging `open`이 실패해 fd를 얻지 못한 경우에도 생성 당시 inode
identity와 현재 entry를 비교해 자신이 만든 빈 staging만 제거하고 lock 제거와 parent fsync까지 수행한다.
파일과 열린 staging의 ownership은 descriptor identity와 directory-entry identity가 일치한 뒤에만 확정한다.
identity 조회가 실패하거나 두 identity가 다르면 현재 경로를 다시 조회한 값으로 ownership을 추정하지 않고 해당
entry를 보존한 채 `EVAL_INTERNAL_ERROR`로 실패한다. 이는 잔존물보다 다른 실행의 replacement 오삭제 방지를
우선하는 fail-closed 규칙이다. cleanup은 대상 이름을 예측 불가능한 격리 이름으로 먼저 exclusive rename하고,
격리된 entry의 identity가 기록한 identity와 일치할 때만 삭제한다. 격리 identity가 다르거나 조회·삭제가 실패하면
원래 이름으로 복원하거나, 원래 이름이 이미 점유됐다면 격리 entry까지 보존한다. 발행 직전에는 열린 staging fd와
이름의 identity, 실제 파일명 set과 생성 시 기록한 각 파일 identity가 정확한 7-file Bundle과 일치하는지 다시
확인하고, 각 파일의 byte length와 SHA-256도 생성 입력과 비교한다. 이름 기반 exclusive rename 직후에도 final
entry와 각 파일의 identity·content fingerprint를 재확인하여 마지막 검사와 syscall 사이에 발생한 교체나 같은
inode의 내용 변조를 성공으로 보고하지 않는다. identity가 다른 replacement는 rollback 대상으로 취급하지 않고
보존하되 자신이 확정한 lock cleanup은 독립적으로 계속 수행한다.
rename 뒤 parent fsync 또는 lock 제거가 실패하면 final directory의 inode ownership을 확인하고 자신이 발행한
Bundle만 rollback한다.
rollback과 lock 정리가 끝난 뒤 parent directory를 다시 fsync하여 제거 상태도 내구성 있게 확정한다.

이 보장은 private allowed root와 예측 불가능한 cleanup 이름을 같은 OS identity에서 실행되는 협조적 publisher들이
준수한다는 신뢰 경계를 전제로 한다. POSIX의 `unlink`/`rmdir`은 열린 descriptor 자체가 아니라 이름을 삭제하므로,
격리 identity 확인 뒤 삭제 syscall 안에서 임의로 이름을 바꿀 수 있는 비협조적 same-UID 프로세스나 syscall
interception까지 replacement 비삭제를 절대 보장하지 않는다. 그런 격리가 필요하면 Runner를 별도 OS identity 또는
전용 mount/프로세스 sandbox에서 실행해야 한다.

## 10. 결정성

결정성은 byte integrity와 semantic equivalence를 구분한다.

- `result-content-manifest.json`은 단일 Run의 실제 파일 bytes와 크기를 고정한다.
- semantic content hash는 반복 실행의 의미 결과를 비교한다.

이번 PR의 semantic projection은 단일 DEV Run의 `run.json`, `cases.jsonl`, `metrics.json`,
`suite-results.json`, `failures.jsonl`만 입력으로 사용한다. 비정본 `report.md`와 byte-integrity 전용
`result-content-manifest.json`은 semantic hash 입력에서 제외한다. 각 기계 payload에서는 schema-aware 방식으로
다음 실행 식별 필드만 제외한다.

- 모든 Artifact record의 `run_id`
- `run.json`의 `started_at`, `completed_at`
- `run.json`의 `result_content_manifest_hash`

Case 결과, 상태, failure code, Dataset·Policy·Suite·config hash, 선택 Case set과 정렬 순서는 제외하지 않는다.
제외 필드는 명시적 allowlist이며 이름 패턴으로 광범위하게 제거하지 않는다. 같은 입력·seed·설정·Fake Adapter로
서로 다른 Run ID와 clock을 사용해 두 번 실행했을 때 semantic hash가 같아야 한다. 의미 필드 하나를 바꾸면
달라져야 한다.

`comparison.json`과 `gate.json`은 Baseline/Candidate Run ID와 hash 자체가 의미 필드이므로 이번 allowlist로
정규화하지 않는다. 후속 비교·Gate 단계에서는 해당 Artifact의 독립 semantic projection과 정규화 규칙을 계약
승인 후 정의한다.

semantic hash는 이번 Schema에 새 필드로 저장하지 않는다. Schema 변경 없이 테스트와 PR 실행 증빙에서
계산·비교한다. Baseline Freeze Receipt에 영구 기록할 필드는 후속 승인 단계에서 별도 계약으로 확정한다.

## 11. Privacy와 의료 안전

- 합성 DEV fixture만 사용한다.
- 실제 환자 질문·답변, 처방, OCR 원문, Provider payload를 입력받지 않는다.
- credential 이름과 값을 config hash나 Artifact에 포함하지 않는다.
- Reporter는 질문·Gold claim·answer text를 출력하지 않는다.
- failure summary는 승인된 비민감 enum만 허용한다.
- 모든 JSON, JSONL record와 Markdown은 발행 전에 privacy validator를 통과한다.
- DEV 결과는 임상 유효성, 외부 의료 검토, Production 또는 공개 승인을 의미하지 않는다.

## 12. 파일 구조

예상 구현 파일의 책임은 다음과 같다.

| 파일 | 책임 |
| --- | --- |
| `ai_worker/tasks/evaluation/runner.py` | 실행 요청, Adapter 경계, Case 선택·정렬, 오류 격리와 상태 집계 |
| `ai_worker/tasks/evaluation/config.py` | DEV execution request model·loader, 참조 경로 검증, resolved config hash |
| `ai_worker/tasks/evaluation/manifest.py` | Artifact serialization·hash, content/semantic manifest 계산 |
| `ai_worker/tasks/evaluation/reporter.py` | typed `ReportData`와 machine entry에서 비민감 Markdown Projection 생성 |
| `ai_worker/tasks/evaluation/cli.py` | `run-dev`, preflight, 안전한 경로·exit code·원자적 발행 연결 |
| `ai_worker/tasks/evaluation/errors.py` | 필요한 안정 오류 code 추가 |
| `ai_worker/tests/evaluation/test_runner.py` | 실행 순서, Adapter 성공·오류·미구현, 상태 집계 |
| `ai_worker/tests/evaluation/test_config.py` | execution request 필드·경로·hash·provenance 검증 |
| `ai_worker/tests/evaluation/test_result_manifest.py` | canonical bytes, content manifest, semantic hash |
| `ai_worker/tests/evaluation/test_reporter.py` | JSON projection, privacy, Markdown 비정본성 |
| `ai_worker/tests/evaluation/test_cli.py` | DEV 실행, HOLDOUT 사전 차단, path·atomic publication |

기존 `schemas/artifacts.py`, exported JSON Schema와 동결 Dataset은 수정하지 않는다.

## 13. 테스트 전략

### 13.1 단위 테스트

- Case ID와 Task Type의 결정적 정렬
- Experiment Type별 정확한 Case 선택
- execution request의 명시적 null, 참조 hash와 resolved config hash
- 선택된 `(case_id, task_type)`별 Adapter 호출이 정확히 1회임을 검증
- `INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED` 집계
- Adapter 예외의 비민감 `CaseResult(ERROR/null)` 변환과 빈 `failures.jsonl`
- 미완료 Answer의 empty-output hash와 Task별 명시적 empty/null 필드
- provenance-bound `input_sha256` preimage와 config 변경 시 hash 변화
- canonical JSONL과 semantic projection
- Markdown이 JSON 상태를 그대로 표시하고 원문을 포함하지 않음

### 13.2 계약 테스트

- 생성 JSON Object를 해당 Pydantic model과 exported JSON Schema 양쪽으로 검증
- `cases.jsonl`, `failures.jsonl` 모든 record의 `schema_id`, `schema_version`, `run_id` 확인
- Task별 비적용 actual 필드의 명시적 `null` 확인
- 미완료 상태의 `decision_status=null`과 계산 필드 `null` 확인
- content manifest의 정렬, count, path allowlist와 self-reference 부재 확인
- `COMPLETED` Run만 content manifest hash를 연결하고 미완료 Run은 `null` 유지

### 13.3 통합 테스트

- 성공 Fake Adapter로 합성 DEV Run 발행
- 한 Case 오류 뒤 다음 Case 계속 실행 및 부모 blocking status 확인
- Adapter 미등록 실행의 `NOT_IMPLEMENTED/null` 확인
- 다른 Run ID·clock으로 2회 실행 후 semantic hash 일치
- 의미 필드 변경 후 semantic hash 불일치
- 생성된 Markdown 변경이 JSON 판정과 hash input을 변경하지 않음

### 13.4 차단·보안 테스트

- 공식 HOLDOUT Manifest가 `load_dataset()` 호출 전에 거부됨
- `SAFETY_REGRESSION`, `AUTHORING`, 혼합 partition 요청 거부
- Dataset ID·status·고정 Case 수에 의존하지 않는 합성 DEV-only Manifest 허용
- automatic retry 설정, `max_attempts != 1`, 같은 Run 내부 Adapter 재호출 거부
- 결과 root 이탈, 절대경로 오용, `..`, symlink ancestor 거부
- 기존 Run directory·lock no-clobber
- short write, fsync, rename 실패 시 최종 directory 미노출
- 예외 메시지·민감 filename·질문·답변 원문 비노출

## 14. 검증 명령

```bash
uv run pytest ai_worker/tests/evaluation -q
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
uv run mypy ai_worker/tasks/evaluation
```

추가로 합성 DEV Run을 서로 다른 Run ID와 clock으로 두 번 실행해 semantic content hash를 비교한다. HOLDOUT
차단 테스트는 loader spy를 사용해 공식 HOLDOUT Manifest가 child resource load 경계에 도달하지 않았음을
증명한다.

## 15. 리뷰와 완료 조건

책임 리뷰어는 평가 상태·nullable 판정·DEV/Release 표현 분리를 검토한다. 교차 리뷰어는 결과가 파일 전용이며
DB schema·migration·환자 Application 저장 경계를 침범하지 않는지 검토한다.

이번 PR의 완료 조건은 다음과 같다.

- 합성 DEV Runner·Reporter가 schema-valid Artifact를 원자적으로 생성한다.
- 동일 의미 입력의 반복 실행 semantic hash가 일치한다.
- resolved evaluation configuration hash가 versioned execution request와 실제 참조 입력 전체를 결속한다.
- Case 오류가 격리되고 모든 blocking status가 보존된다.
- 각 Case·Task Adapter 호출은 한 Run에서 정확히 1회이며 재실행은 새 Run ID를 요구한다.
- 미구현·미실행을 PASS·FAIL·INCONCLUSIVE로 위장하지 않는다.
- HOLDOUT·SAFETY_REGRESSION이 Loader 전에 차단된다.
- 실제 환자정보·원문·credential이 Artifact와 로그에 없다.
- DB·API·동결 Dataset·Artifact Schema에 변경이 없다.

이 완료는 #157 전체 Close 조건이 아니다. 승인된 Policy로 최초 HOLDOUT Baseline을 실행하고 불변 Baseline
Receipt와 반복 실행 결정성 증빙을 남기는 후속 단계가 완료돼야 #158~#163의 비교·Gate 입력이 된다.
