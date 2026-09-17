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

0. fixture가 선언한 sentinel이 실제 제출 query와 색인된 Source에 결속돼 있는지 증명한다 (§7.1).
0. pinned Index가 승인된 합성 Index인지 DB로 증명한다 (§7.2). 미증명이면 Provider 호출과
   Run 생성 이전에 차단한다.
0. 자원 관측을 실행 전에 검증·기록한다 — 관측 존재, `memory_usage_bytes > 0`, limit 1 GiB,
   `OOMKilled=false`. 무효면 interim을 만들지 않고 FAILED다. finalize도 interim의 자원 증빙을
   다시 확인하므로 privacy만 깨끗하다고 SUCCESS가 되지 않는다.
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
9. Evidence Gate가 **실제로 선택한** candidate의 `knowledge_chunk_id`를 꺼내 그 chunk 본문이
   Source sentinel을 갖는지 독립 session으로 확인한다. allowed corpus 어딘가에 marker가 있다는
   사실은 이 검증을 대신하지 못한다 — marker 없는 다른 chunk가 실제로 선택될 수 있다.
10. Evidence Gate negative case 2건 — stale(Source currentness 불일치)과 locator mismatch.
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

필수 대상은 `ai_worker_logs`, `fastapi_logs`, `redis_stream`, `redis_dlq`,
`smoke_one_shot_logs`다. 마지막 항목은 질문을 실제로 제출하는 one-shot 컨테이너 자신의 로그이며
이것도 유출 지점이므로 필수다. 필수 대상이
`NOT_EXECUTED`이거나 `NOT_APPLICABLE`이면 `SUCCESS`가 될 수 없다. 현재 배포에는 retrieval 전용
quarantine 저장소가 없으므로 `quarantine`은 `NOT_APPLICABLE`로 기록한다.

sentinel 원문은 artifact에 남기지 않으며 `query_sentinel_sha256`, `source_sentinel_sha256`만
기록한다.

## 6. 실행 명령 — host 관측 + container 실행 2단계

> **미검증 표시**: 아래 절차는 실제 EC2와 빌드된 image에서 **아직 실행해 본 적이 없다.**
> 이 PR은 조립과 로컬 회귀까지만 검증했다. 실제 EC2 실행은 후속에서 확인한다.

retrieval 실행은 DB와 embedding provider에 닿아야 하므로 **application container**에서,
배포 관측(`docker inspect` / `stats` / `logs`)은 Docker CLI가 이미 있는 **EC2 host**에서 수행한다.
runtime container에 Docker daemon socket을 mount하지 않는다. 그것은 관측 수단이 아니라
권한 상승이다.

### 6.1 host 단계 A — 배포 관측 (실행 전)

EC2 host에서 실행한다. 컨테이너 identity와 자원 snapshot만 담는다. **privacy scan은 여기에
포함하지 않는다** — 실행 전 scan은 그 실행이 만든 유출을 볼 수 없다.

```bash
MEM=$(docker inspect -f '{{.HostConfig.Memory}}' ai-worker)
USAGE_RAW=$(docker stats --no-stream --format '{{.MemUsage}}' ai-worker | cut -d/ -f1 | tr -d ' ')
USAGE=$(numfmt --from=iec "${USAGE_RAW%i*}i")   # 실패하면 여기서 중단한다. 0 fallback 금지.
test "${USAGE:-0}" -gt 0 || { echo "memory conversion failed" >&2; exit 1; }

cat > observation.json <<JSON
{
  "schema_version": "ret-h-aws-smoke-observation-v1",
  "image_digest": "$(docker inspect -f '{{index .RepoDigests 0}}' "$(docker inspect -f '{{.Image}}' ai-worker)")",
  "worker": {
    "image": "$(docker inspect -f '{{.Config.Image}}' ai-worker)",
    "image_id": "$(docker inspect -f '{{.Image}}' ai-worker)",
    "memory_limit_bytes": ${MEM},
    "restart_count": $(docker inspect -f '{{.RestartCount}}' ai-worker),
    "oom_killed": $(docker inspect -f '{{.State.OOMKilled}}' ai-worker),
    "state_status": "$(docker inspect -f '{{.State.Status}}' ai-worker)",
    "health_status": "$(docker inspect -f '{{.State.Health.Status}}' ai-worker)"
  },
  "resources": { "memory_usage_bytes": ${USAGE}, "memory_limit_bytes": ${MEM},
                 "cpu_percent": $(docker stats --no-stream --format '{{.CPUPerc}}' ai-worker | tr -d '%') }
}
JSON
```

단위 변환이 실패하면 **0으로 대체하지 않고 중단한다.** runner도 `memory_usage_bytes <= 0`을
거부한다 — 실패한 변환을 실측치로 기록하지 않기 위해서다.

### 6.2 container 단계 — 실제 RET-H 실행 (execute)

```bash
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)          # host scan 창의 시작점
ONESHOT="ret-h-smoke-oneshot-$(date -u +%Y%m%d%H%M%S)-$RANDOM"   # run별 고유 이름

# --rm 을 쓰지 않는다. --rm 은 종료 즉시 컨테이너를 지워 로그까지 없애므로
# 필수 scan 대상인 smoke_one_shot_logs 가 영구히 NOT_EXECUTED 가 된다.
docker compose --env-file envs/.prod.env -f infra/docker/docker-compose.prod.yml \
  run --no-deps -T --name "$ONESHOT" \
  -v "$PWD/ret-h-smoke-fixture.json:/smoke/fixture.json:ro" \
  -v "$PWD/observation.json:/smoke/observation.json:ro" \
  -v "$PWD/smoke-out:/smoke/out" \
  fastapi \
  uv run --no-sync python -m scripts.ret_h_aws_synthetic_smoke \
    --mode aws-live-execute \
    --git-commit-sha "$(git rev-parse HEAD)" \
    --fixture-manifest /smoke/fixture.json \
    --observation-file /smoke/observation.json \
    --one-shot-container "$ONESHOT" \
    --output-path /smoke/out/interim.json
```

컨테이너는 **scan이 끝날 때까지 보존**한다. 정리는 §6.5에서 명시적으로 수행하며 실패를 숨기지
않는다.

execute 단계는 **절대 SUCCESS를 내지 않는다.** `AWS_SMOKE_NOT_EXECUTED` /
`BLOCKED_BY_AWAITING_PRIVACY_OBSERVATION`으로 끝나며 `retrieval_run_id`와
`execution_started_at` / `execution_finished_at`을 기록한다.

### 6.3 host 단계 B — 실행 후 privacy scan (bound)

실행이 끝난 뒤 scan한다. 문서는 **이번 실행에 결속**된다 — Retrieval Run ID, sentinel digest,
조회 창. one-shot 컨테이너 자신의 로그도 필수 대상이다.

```bash
RUN_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["retrieval"]["retrieval_run_id"])' smoke-out/interim.json)
QS=<fixture의 query_sentinel>; SS=<fixture의 source_sentinel>

scan() {  # 인자: 로그를 내보내는 명령
  if out=$("$@" 2>&1); then
    if printf '%s' "$out" | grep -qF -e "$QS" -e "$SS"; then echo FOUND; else echo SCANNED_AND_NOT_FOUND; fi
  else echo NOT_EXECUTED; fi
}

cat > privacy.json <<JSON
{
  "schema_version": "ret-h-aws-smoke-privacy-observation-v1",
  "retrieval_run_id": "${RUN_ID}",
  "query_sentinel_sha256": "$(printf '%s' "$QS" | shasum -a 256 | cut -d' ' -f1)",
  "source_sentinel_sha256": "$(printf '%s' "$SS" | shasum -a 256 | cut -d' ' -f1)",
  "scanned_since": "${SINCE}",
  "scanned_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "targets": {
    "ai_worker_logs":      "$(scan docker logs --since "$SINCE" --no-color ai-worker)",
    "fastapi_logs":        "$(scan docker logs --since "$SINCE" --no-color fastapi)",
    "smoke_one_shot_logs": "$(scan docker logs --no-color "$ONESHOT")",
    "redis_stream":        "$(scan docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning XREVRANGE oryak:jobs + - COUNT 500')",
    "redis_dlq":           "$(scan docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning XREVRANGE oryak:jobs:dead-letter + - COUNT 500')",
    "quarantine":          "NOT_APPLICABLE"
  }
}
JSON
```

`scan`은 stdout과 stderr를 모두 본다. `docker logs`는 컨테이너 stderr를 자신의 stderr로
재생하고 Python logging도 기본이 stderr이므로, stdout만 보면 유출이 가장 잘 드러나는 stream을
놓친다. 원문 로그는 host를 떠나지 않으며 문서에는 4상태만 남는다.

### 6.4 container 단계 — finalize

```bash
docker compose --env-file envs/.prod.env -f infra/docker/docker-compose.prod.yml \
  run --rm --no-deps -T \
  -v "$PWD/ret-h-smoke-fixture.json:/smoke/fixture.json:ro" \
  -v "$PWD/privacy.json:/smoke/privacy.json:ro" \
  -v "$PWD/smoke-out:/smoke/out" \
  fastapi \
  uv run --no-sync python -m scripts.ret_h_aws_synthetic_smoke \
    --mode aws-live-finalize \
    --fixture-manifest /smoke/fixture.json \
    --interim-artifact /smoke/out/interim.json \
    --privacy-observation-file /smoke/privacy.json \
    --output-path /smoke/out/ret-h-aws-synthetic-smoke.json
```

finalize는 결속을 먼저 확인한다. Run ID 불일치, sentinel digest 불일치, 실행 시작 이후에
시작된 scan 창, 실행 종료 이전에 찍힌 scan은 모두 `FAILED_BY_PRIVACY_OBSERVATION_UNBOUND`다.
다른 실행의 깨끗한 scan으로 이번 실행을 통과시킬 수 없다.

### 6.5 cleanup — scan 이후에만

```bash
docker rm "$ONESHOT" || { echo "one-shot cleanup failed: $ONESHOT" >&2; exit 1; }
```

정리는 **scan과 finalize가 끝난 뒤**에만 수행한다. 실패를 조용히 넘기지 않는다. 이름이 run별로
고유하므로 이전 실행이 남긴 컨테이너와 충돌하지 않는다.

`scan_and_cleanup_one_shot()`이 같은 순서(logs → rm)를 코드로 고정하며, 단위 테스트가 argv와
순서를 검증한다. 새 daemon/socket 권한은 추가하지 않는다.

`backend/app/Dockerfile`이 `scripts/ret_h_aws_synthetic_smoke.py`를 image에 포함한다.
image에 Docker CLI는 추가하지 않는다.

관측 대상 구분에 주의한다. **elapsed_ms는 application one-shot container의 실행 시간**이고,
**memory/CPU/OOM/restart는 별도 `ai-worker` container의 snapshot**이다. 둘은 같은 프로세스가
아니며 artifact도 이를 분리해 기록한다.

## 7. fixture manifest

`--fixture-manifest`는 **별도 credential로 수행된 one-shot bootstrap**이 생성한 합성 실행
binding을 가리킨다. smoke 자체는 Knowledge Index를 생성하지 않으며 runtime identity로만 동작한다.

필수 key: `knowledge_index_id`, `knowledge_index_ref`, `allowed_source_snapshot_ids`,
`allowed_source_snapshot_member_ids`, `synthetic_query`, `synthetic_query_sha256`,
`query_sentinel`, `source_sentinel`,
`job_id`, `execution_context_id`, `prescription_version_id`, `runtime_release_bundle_id`,
`runtime_release_bundle_manifest_hash`, `runtime_execution_manifest_id`,
`runtime_execution_manifest_hash`, `runtime_guard_decision_ref`, `source_manifest_hash`,
그리고 `filter_snapshot_ref` / `evidence_index_ref` / `lexical_config_ref` / `dense_config_ref` /
`retrieval_config_ref` / `embedding_adapter_ref` / `search_adapter_ref`
(각각 `artifact_code`, `version`, `content_sha256`).

### 7.1 sentinel은 fixture가 선언하고 runner가 결속을 증명한다

sentinel은 bootstrap이 생성해 **실제 query 문자열과 색인된 Source 본문에 심고** manifest에
기록한다. runner는 자체 sentinel을 만들지 않는다. 무작위로 만든 문자열은 제출된 query에도
corpus에도 없으므로 그 scan은 항상 통과하는 공허한 검사가 된다.

실행 전에 두 결속을 증명하고, 하나라도 증명되지 않으면 Provider 호출과 Run 생성 이전에
`BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED`로 차단한다.

- 제출 query가 **승인된 `synthetic_query_sha256`과 정확히 일치**한다. marker 포함만으로는
  부족하다 — marker를 붙인 다른 질문도 통과해 embedding provider로 전송될 수 있다.
- `synthetic_query`가 `query_sentinel`을 실제로 포함한다.
- **선언된 allowed member에 속한** chunk 본문 중 최소 하나가 `source_sentinel`을 포함한다
  (read-only DB 조회). 검색하지 않는 chunk의 marker로 증빙을 만들 수 없도록 범위를 제한한다.
  비교는 `LIKE`가 아니라 `strpos` 리터럴 검색이다. `LIKE`는 sentinel 안의 `_`/`%`를 wildcard로
  취급해 marker 없는 corpus도 통과시킨다.

### 7.2 synthetic-only는 DB로 증명한다

manifest가 스스로를 "synthetic"이라 부르는 것은 증거가 아니다. 실행 전에 read-only로 확인한다.

- pinned `knowledge_index_id`가 DB에 존재하고 `index_code`가 승인된 합성 index code다.
  승인 목록은 PR #663이 이미 정한 `SYNTHETIC_INDEX_CODE`를 read-only로 재사용하며 새 규약을
  만들지 않는다.
- 해당 index의 모든 member snapshot이 manifest가 선언한 allow-list 안에 있다.

증명되지 않으면 `BLOCKED_BY_NON_SYNTHETIC_FIXTURE`로 차단한다. `PUBLIC_TRACK_F=false`는 공개
여부만 보므로 이 검사를 대신하지 못한다.

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
