# Issue #368 Protected Retrieval Infrastructure Adapter

> 저장소 구현 증빙입니다. 실제 protected 환경 활성화나 HOLDOUT 접근 승인 증빙이 아닙니다.

## 상태

- Repository adapter: `PARTIALLY_IMPLEMENTED`
- Authorization control C1: `IMPLEMENTED_IN_REPOSITORY`
- Implemented scope: approval ingestion and grant/revoke/expire transaction·audit services
- Production approval source connector: `NOT_IMPLEMENTED`
- Dataset lifecycle/FREEZE service: `NOT_IMPLEMENTED`
- Identity registration/disable service: `NOT_IMPLEMENTED`
- Effective enforcement: `NOT_IMPLEMENTED`
- Access authorized: `false`
- HOLDOUT authored: `false`
- Freeze recorded: `false`
- Actual run: `NOT_CREATED`
- Disposal: `BLOCKED_BY_ISSUE_425`
- Release eligible: `false`

## 검증

- Authorization-control related suite: `128 passed`
- Runtime assembly suite: `24 passed`
- Protected-off Worker image import: `passed`
- Protected limited-login verification in CI: `passed`
- 실제 환경 좌표와 보호 데이터는 사용하지 않았습니다.

## Evidence self hash 입력

`evidence_sha256`은 아래 구현 파일의 raw SHA-256을 포함해 계산합니다.
구현 파일이 바뀌면 이 문서의 본문이 같아도 해시는 달라집니다.

| Component | Path | raw_sha256 |
| --- | --- | --- |
| `ASYNC_KERNEL_SEAM` | `ai_worker/tasks/evaluation/protected_retrieval.py` | `26d068434d9ec5147a65302aab57958b689855e86e9173659e7489abb1675c38` |
| `POSTGRESQL_ADAPTER` | `ai_worker/adapters/postgresql_protected_retrieval.py` | `5936abbe9f3a9136760618d082f6dae010b620d647056b075436e19f1a89969b` |
| `FAIL_CLOSED_CONFIG` | `ai_worker/core/config.py` | `4bb5ec095fae68b9f0fc7de29096b5ad848aef0a4eee06b6a8c849b917e92c81` |
| `EXPLICIT_RUNTIME_ASSEMBLY` | `ai_worker/core/runtime_assembly.py` | `2b91bcf9983e9a00c9c8f07e48a034dd5a68806859ebe3cc508132d78871c2a4` |
| `PROTECTED_ROLE_POLICY` | `infra/python/protected_retrieval_role_policy.py` | `597bf68229c5830578ddba07aa3652cace08ec3e2474031ef6250cccd91d82a4` |
| `ISOLATED_MIGRATION_ENV` | `infra/protected_retrieval/env.py` | `4853f8178ea611df177900573e6593c8bdc76db86a039ebd6f299013895fdbf3` |
| `ISOLATED_MIGRATION` | `infra/protected_retrieval/versions/368000000001_create_protected_retrieval.py` | `20ed57547853a39ae5254d69dd71873e0a26223a705c5231e50dc8aa7416c68d` |
| `AUTHORIZATION_CONTROL_CONTRACT` | `ai_worker/tasks/evaluation/protected_retrieval_control.py` | `c56d10d3de6228f46cf1a199fd49e81342254001964dbfc14f29ed2ba36042e9` |
| `POSTGRESQL_CONTROL_ADAPTER` | `ai_worker/adapters/postgresql_protected_retrieval_control.py` | `1ea6db8887efc3822686e4562dc8310ae7ac8b73e1a8149862ee6c859386c615` |
| `AUTHORIZATION_CONTROL_MIGRATION` | `infra/protected_retrieval/versions/368000000002_add_authorization_control.py` | `56a7275ebc83ebf69f115230e36e40bc13d0731a7c91bed900ff287b2b42c5b8` |

재생성·검증: `uv run python scripts/verify_protected_runner_evidence.py [--write]`

## 활성화 전 필수 조건

- `EXT-PRIV-001` 승인
- 실제 환경 provisioning 및 독립 Backend·Security 검증
- backup·restore·rotation 운영 증빙
- Track F external gate 충족

Evidence self hash: `b54edbe8fd6b9094da59fb20c0b80be428585f452213d03572f8f176a0df60a1`
