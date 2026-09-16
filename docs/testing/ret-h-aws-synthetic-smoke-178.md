# RET-H AWS Synthetic Deployment Smoke (Issue #178)

이 문서는 Issue #178의 남은 범위인 **RET-H AWS 합성 배포 smoke**의 실행 방법과 증빙 artifact
형식을 기록한다. 이 smoke는 **배포 가능성(deployability) 증빙**이며 품질 평가도 Release 판정도
아니다. `PUBLIC_TRACK_F_ENABLED`는 계속 `false`로 유지하고 active Bundle pointer는 변경하지 않는다.

## 1. 배포 경계

정본 배포 구조는 `docs/runbooks/aws-production-demo.md`의 단일 EC2 + Docker Compose다.

- Region `ap-northeast-2`, EC2 `t3.medium` 1대
- `infra/docker/docker-compose.prod.yml`
- `ai-worker` `mem_limit: 1G` (1 GiB)
- ECS · CloudWatch · SQS는 이 배포에 존재하지 않으며 smoke도 새로 도입하지 않는다.

## 2. 구성 요소

| 파일 | 책임 |
| --- | --- |
| `backend/app/release_validation/ret_h_synthetic_smoke.py` | fail-closed orchestration, preflight, privacy scan, artifact writer. `ai_worker` import 없음 |
| `ai_worker/tasks/evaluation/ret_h_smoke.py` | production dependency 조립, canonical receipt 검증, Evidence Gate negative case |
| `scripts/ret_h_aws_synthetic_smoke.py` | 두 경계를 잇는 EC2 one-shot composition root |

`app` 이미지에는 `app`과 `ai_worker`가 모두 포함되므로(`backend/app/Dockerfile`) smoke는 `app`
이미지의 one-shot 컨테이너에서 실행한다. `ai` 이미지에는 `backend/app`이 없다.

## 3. fail-closed 원칙

검증 flag는 **실제로 검증을 수행해 통과한 경우에만** `true`가 된다. 수행하지 못한 검증은 절대
PASS로 기록하지 않는다. 최종 상태는 셋 중 하나다.

- `AWS_SMOKE_NOT_EXECUTED` — preflight 차단 또는 production dependency 미조립 (아무것도 실행되지 않음)
- `FAILED` — 실행했으나 검증 실패 또는 필수 검증 미수행
- `SUCCESS` — 아래 모든 항목을 실제로 실행하고 통과

차단/실패 코드는 `blocked_code`에 canonical 문자열로 남는다. embedding credential 부재는 설계
문서가 정한 `BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL`을 사용한다.

## 4. 실행 순서

1. `PUBLIC_TRACK_F_ENABLED`가 증명 가능하게 `false`인지 확인 (`false`/`False`/`FALSE`/`0` 허용).
   값을 읽을 수 없으면 묵시적 false로 간주하지 않고 차단한다.
2. 승인된 OpenAI embedding credential 존재 확인. **존재 여부만** 확인하며 값은 로그·artifact·
   예외 어디에도 남기지 않는다. 없으면 fake embedding으로 fallback하지 않고 차단한다.
3. 실행 중인 `ai-worker` 컨테이너 identity 관측 (`docker inspect`). allowlist 필드만 읽고
   전체 environment는 dump하지 않는다.
4. memory limit이 1 GiB인지, `OOMKilled=false`인지 확인.
5. production dependency 전체 조립 확인. 하나라도 없으면 실행 전에 차단한다.
6. production `execute_hybrid_retrieve`로 실제 RET-H 실행 (real PostgreSQL / pgvector /
   Evidence Search / OpenAI embedding adapter / Evidence Gate / Retrieval Run Store).
7. **독립 read-only session**으로 `retrieval_run`(`COMPLETED`, `RET-H`, receipt hash),
   `retrieval_signal`(lexical 계열 1건 이상 + `DENSE` 1건 이상), `retrieval_hit`(`selected` 1건 이상) 검증.
8. canonical `compute_receipt_hash` 재계산으로 receipt 검증. 문자열 비교만 하지 않는다.
9. Evidence Gate negative case 2건 — stale(Source currentness 불일치)과 locator mismatch.
   실제 production `post_search` + `evaluate_evidence_gate`를 통과시키며, production row는
   변조하지 않고 후보의 in-memory provenance만 교란한다.
10. raw query/Source sentinel 비로그 검사.
11. resource observation (`docker stats --no-stream`).

## 5. privacy scan 상태

대상별로 다음 네 상태를 구분한다.

- `SCANNED_AND_NOT_FOUND` — 실제로 조회했고 sentinel이 없음
- `FOUND` — sentinel 발견 (즉시 `FAILED`)
- `NOT_APPLICABLE` — 이 배포에 해당 저장소가 존재하지 않음
- `NOT_EXECUTED` — 조회하지 못함 (PASS 아님)

필수 대상은 `ai_worker_logs`, `fastapi_logs`, `redis_stream`, `redis_dlq`다. 필수 대상이
`NOT_EXECUTED`이거나 `NOT_APPLICABLE`이면 `SUCCESS`가 될 수 없다. 현재 배포에는 retrieval 전용
quarantine 저장소가 없으므로 `quarantine`은 `NOT_APPLICABLE`로 기록한다.

sentinel 원문은 artifact에 남기지 않으며 `query_sentinel_sha256`, `source_sentinel_sha256`만
기록한다.

## 6. 실행 명령

```bash
docker compose \
  --env-file envs/.prod.env \
  -f infra/docker/docker-compose.prod.yml \
  run --rm --no-deps -T fastapi \
  uv run --no-sync python -m scripts.ret_h_aws_synthetic_smoke \
    --mode aws-live \
    --git-commit-sha "$(git rev-parse HEAD)" \
    --fixture-manifest /app/ret-h-smoke-fixture.json \
    --output-path /app/ret-h-aws-synthetic-smoke.json
```

새 permanent service를 만들지 않는 one-shot 실행이다. `--mode`를 생략하면 `local-preflight`이며
절대 실행되지 않는다.

## 7. fixture manifest

`--fixture-manifest`는 **별도 credential로 수행된 one-shot bootstrap**이 생성한 합성 실행
binding을 가리킨다. smoke 자체는 Knowledge Index를 생성하지 않으며 runtime identity로만 동작한다.

필수 key: `knowledge_index_id`, `knowledge_index_ref`, `allowed_source_snapshot_ids`,
`allowed_source_snapshot_member_ids`, `synthetic_query`, `job_id`, `execution_context_id`,
`prescription_version_id`, `runtime_release_bundle_id`,
`runtime_release_bundle_manifest_hash`, `runtime_execution_manifest_id`,
`runtime_execution_manifest_hash`, `runtime_guard_decision_ref`, `source_manifest_hash`,
그리고 `filter_snapshot_ref` / `evidence_index_ref` / `lexical_config_ref` / `dense_config_ref` /
`retrieval_config_ref` / `embedding_adapter_ref` / `search_adapter_ref`
(각각 `artifact_code`, `version`, `content_sha256`).

## 8. DB identity 분리

- regular `ai-worker`에 Knowledge Index Builder 권한을 추가하지 않는다.
  `tests/contract/test_database_role_deployment.py`가 이 경계를 고정한다.
- runtime identity(`DB_APP_USER`)는 Retrieval Run만 생성한다.
- 합성 Knowledge Index 생성은 runtime과 다른 credential의 one-shot bootstrap이 담당한다.

**현재 미해결 blocker (이번 범위 밖, 별도 후속 Issue)**: PR #663의
`bootstrap_dev_knowledge_index`는 하나의 engine으로 `rag_source*`와
`knowledge_*` / `rag_knowledge_index*`를 모두 write한다. 현재 역할 모델에서 `SOURCE_WRITER`는
`rag_source*`만, `KNOWLEDGE_INDEX_BUILDER`는 `knowledge_*`만 write할 수 있으므로 단일 승인
identity로 이 bootstrap을 실행할 수 없다.

이 authority split은 이번 diff에서 구현하지 않는다. 후속 Issue 초안은
`docs/designs/ceohwj/issue-178-followup-bootstrap-authority-split.md`에 있다. 해당 후속이
해소되기 전까지 live AWS smoke는 `SUCCESS`가 될 수 없고, smoke는 fixture manifest 부재를
`AWS_SMOKE_NOT_EXECUTED`로 fail-closed 처리한다.

## 9. artifact 형식

### 9.0 상태: validation-only, shared contract 아님

`ret-h-aws-synthetic-smoke-v1`은 **사람이 읽는 validation 증빙 artifact**다. 현재 이 artifact를
읽는 machine consumer(Runtime, Frontend, Evaluation runner, CI gate, 외부 API)가 없으므로
`docs/contracts/` 승격 대상이 아니며 이 문서에만 기록한다.

승격 조건: 이 artifact를 **프로그램이 소비**하기 시작하면 — 예를 들어 CI gate가 `status`로 분기하거나,
Release 판정 파이프라인이 필드를 파싱하거나, 다른 도메인이 이 JSON을 입력으로 받으면 — 그 시점에
shared contract가 되므로 `docs/contracts/`에 등록하고 `docs/contracts/README.md`에 색인한다.
그 전까지는 이 문서가 유일한 정본이다.

### 9.1 필드

`schema_version: "ret-h-aws-synthetic-smoke-v1"`. 최상위 key는 `status`, `mode`, `started_at`,
`finished_at`, `git_commit_sha`, `blocked_code`, `error_message`, `deployment`, `fixture`,
`retrieval`, `evidence_gate`, `privacy`, `resources`, `limitations`, `details`다.

`limitations`는 항상 다음을 포함한다.

- synthetic deployment smoke only
- not a quality benchmark
- not a release approval
- not public activation
- RET-HR not executed

`retrieval.reranker_calls`는 항상 `0`이며 `retrieval.variant`는 항상 `RET-H`다.

## 10. 검증

```bash
uv run pytest tests/services/test_ret_h_synthetic_smoke.py -q
uv run pytest ai_worker/tests/rag/test_ret_h_smoke_composition.py -q
uv run pytest tests/integration/rag/test_ret_h_aws_smoke_verification.py -q
uv run ruff check backend/app/release_validation ai_worker/tasks/evaluation tests/services tests/integration/rag
uv run mypy backend/app/release_validation ai_worker/tasks/rag ai_worker/tasks/evaluation
```

`tests/integration/rag/test_ret_h_aws_smoke_verification.py`는 실제 PostgreSQL에 migration을
적용하고 production `SqlAlchemyRetrievalRunStore`로 row를 저장한 뒤 smoke의 독립 검증 session이
terminal state·signal 계열·selected hit·receipt hash를 실제로 판별하는지 확인한다.
CI/unit test는 실제 OpenAI를 호출하지 않는다.
