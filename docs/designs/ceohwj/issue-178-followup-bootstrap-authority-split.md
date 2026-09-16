# 후속 Issue 초안 — Synthetic Knowledge Index Bootstrap Authority Split

> **상태: 초안(Draft). 이 문서는 후속 Issue 본문 초안이며 구현 지시가 아니다.**
> Issue #178의 RET-H AWS synthetic smoke diff에서는 이 항목을 **구현하지 않는다.**
> 담당 리뷰어 확인 후 별도 Issue로 등록하고 별도 PR에서 구현한다.

## 1. 배경

Issue #178의 RET-H AWS synthetic deployment smoke는 실제 EC2/Docker Compose 배포에서
production `execute_hybrid_retrieve`를 한 번 실행하고 fail-closed 증빙을 남긴다.
이 실행에는 합성 Knowledge Index가 선행되어야 한다.

smoke runner 자체(`scripts/ret_h_aws_synthetic_smoke.py`)는 runtime identity로만 동작하며
Knowledge Index를 생성하지 않는다. Index는 **별도 credential의 one-shot bootstrap**이 만들고
smoke는 그 결과를 `--fixture-manifest`로 읽기만 한다. 그 bootstrap 경로가 아직 없다.

## 2. 문제

PR #663이 병합한 `ai_worker/tasks/evaluation/actual_retrieval_index.py`의
`bootstrap_dev_knowledge_index(engine, ...)`는 **단일 engine**으로 다음을 모두 write한다.

`SqlAlchemyEvaluationBootstrapRepository` 기준:

- `rag_source`
- `rag_source_endpoint`
- `rag_source_operation`
- `rag_source_snapshot`
- `rag_source_snapshot_verification`
- `knowledge_document`
- `knowledge_chunk`

그리고 `SqlAlchemyKnowledgeEvidenceIndexRepository`를 통해:

- `rag_knowledge_index`
- `rag_knowledge_index_member`

현재 최소권한 역할 모델(`infra/python/source_role_policy.py`,
`infra/python/knowledge_index_role_policy.py`)은 다음과 같다.

| 역할 | `rag_source*` | `knowledge_*` / `rag_knowledge_index*` |
| --- | --- | --- |
| `DB_APP_USER` (runtime) | SELECT | SELECT |
| `SOURCE_WRITER_USER` | SELECT, INSERT (+ 제한적 UPDATE, lock marker) | 권한 없음 |
| `KNOWLEDGE_INDEX_BUILDER_USER` | SELECT + lock marker UPDATE만 | INSERT 가능 |

따라서 **현재 승인된 어떤 단일 identity도 이 bootstrap 전체를 실행할 수 없다.**

- `SOURCE_WRITER`로 실행 → `knowledge_document` INSERT에서 권한 거부
- `KNOWLEDGE_INDEX_BUILDER`로 실행 → `rag_source` INSERT에서 권한 거부
- `DB_APP_USER`로 실행 → 첫 INSERT에서 권한 거부

DEV/CI에서 이 문제가 드러나지 않은 이유는 통합 테스트가 임시 DB의 소유자 계정으로 실행되어
역할 경계가 적용되지 않기 때문이다.

## 3. 해서는 안 되는 해결

다음은 모두 배제한다.

- `DB_APP_USER` 권한 확대
- regular `ai-worker` 컨테이너에 builder 또는 writer credential 주입
  (`tests/contract/test_database_role_deployment.py`가 이 경계를 고정하고 있다)
- admin / migration credential을 worker에 전달
- 세 identity를 하나로 합친 새 "bootstrap" superuser 역할 신설
- 합성 Index 생성을 위해 RLS·Trigger·Stored Procedure 도입 (AGENTS.md 금지)

## 4. 후보 방향 (택일은 후속 Issue에서 결정)

### 4.1 2단계 one-shot bootstrap (선호)

합성 fixture 생성을 두 개의 순차 one-shot Compose 실행으로 나눈다.

1. `SOURCE_WRITER` identity — 합성 `rag_source` 계층과 snapshot/member를 생성하고 seal한 뒤 commit
2. `KNOWLEDGE_INDEX_BUILDER` identity — 1단계가 만든 snapshot member를 read-only로 재검증하고
   `knowledge_document` / `knowledge_chunk` / `rag_knowledge_index` / `rag_knowledge_index_member`를 생성

기존 `source-writer`(profile `source-admin`) 서비스가 1단계의 선례다. 2단계에 대응하는
`knowledge-index-builder` one-shot 서비스는 아직 없다.

- 장점: 권한 확대 없음, 기존 역할 경계 그대로
- 비용: `bootstrap_dev_knowledge_index`를 두 단계로 분리해야 함. 이 파일은 #273/#678 소유이므로
  **소유자 조율이 선행되어야 한다.** 기존 DEV 경로의 semantics를 바꾸지 않는 추가 진입점 형태가 바람직하다.

### 4.2 smoke 전용 최소 fixture

#273 DEV 100문장 Index를 재사용하지 않고, smoke 전용의 훨씬 작은 합성 Index(예: 소수 chunk)를
같은 2단계 identity 분리로 생성한다.

- 장점: #273 소유 코드를 건드리지 않음
- 비용: 합성 fixture 경로가 둘로 늘어남 (중복 synthetic retrieval stack 금지 원칙과 긴장)

### 4.3 운영자 수동 절차

Runbook에 SQL/명령 절차만 문서화하고 코드로 만들지 않는다.

- 장점: 코드 변경 없음
- 비용: 재현성·검증 가능성이 낮아 배포 증빙으로서 가치가 떨어짐

## 5. 함께 결정해야 할 것

합성 실행 binding은 `retrieval_run` FK 때문에 `ai_job` row를 필요로 하고, `ai_job.user_id`는
`user` FK를 요구한다. 즉 **운영 DB에 합성 `user` / `ai_job` row를 쓰게 된다.**
(`retrieval_run`의 `execution_context_id`, `prescription_version_id`,
`runtime_release_bundle_id`, `runtime_execution_manifest_id`에는 FK가 없다.)

이 row들은 runtime identity로 쓸 수 있으므로 권한 문제는 아니지만, 운영 DB에 합성 레코드를
남기는 결정이므로 다음을 후속 Issue에서 함께 정한다.

- 합성 `user` / `ai_job`의 식별 규약 (고정 UUID namespace, 명시적 synthetic 표식)
- 정리(cleanup) 정책과 담당
- 실제 사용자 통계·조회에 섞이지 않는다는 보장

실제 환자/처방 데이터는 어떤 경우에도 사용하지 않는다.

## 6. 완료 기준 초안

- [ ] 합성 Knowledge Index bootstrap이 `SOURCE_WRITER`와 `KNOWLEDGE_INDEX_BUILDER` 두 identity로
      분리 실행된다.
- [ ] regular `ai-worker`의 DB 권한이 확대되지 않는다
      (`tests/contract/test_database_role_deployment.py` 유지·강화).
- [ ] bootstrap 결과가 `scripts/ret_h_aws_synthetic_smoke.py`의 `--fixture-manifest` 형식으로 산출된다.
- [ ] 합성 `user` / `ai_job` 식별 규약과 정리 정책이 문서화된다.
- [ ] 최소권한 역할이 실제로 적용된 DB에서 bootstrap 통합 테스트가 통과한다
      (소유자 계정이 아닌 provisioned role로 실행).
- [ ] #273 DEV dataset, `#678` evaluation semantics, `evals/**`는 변경되지 않는다.

## 7. 선행·조율

- 소유·리뷰: Issue #178 기준 송은영 (`@phina-io`)
- `actual_retrieval_index.py` 분리가 필요하면 #273/#678 소유자와 사전 조율
- `PUBLIC_TRACK_F_ENABLED`는 계속 `false`, active Bundle pointer 변경 없음
- 이 후속이 해소되기 전에는 RET-H AWS live smoke가 `SUCCESS`가 될 수 없고
  `AWS_SMOKE_NOT_EXECUTED`로 fail-closed 유지된다.

## 8. 관련

- Issue #178, PR #663 (`bootstrap_dev_knowledge_index`), PR #482 (최소권한 Builder)
- `docs/testing/ret-h-aws-synthetic-smoke-178.md` §8
- `infra/python/knowledge_index_role_policy.py`, `infra/python/source_role_policy.py`
- `tests/contract/test_database_role_deployment.py`
