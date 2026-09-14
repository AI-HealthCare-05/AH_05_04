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
| `ASYNC_KERNEL_SEAM` | `ai_worker/tasks/evaluation/protected_retrieval.py` | `cd5dcf33ce3e27f280fd9c42ac460ab6f519b4c5fda38df49a1a4de5469e6d18` |
| `POSTGRESQL_ADAPTER` | `ai_worker/adapters/postgresql_protected_retrieval.py` | `c3d2cd602bd245a04a6ca16bed45ecb0a6cf64a2802ca842524310a9161d0a16` |
| `FAIL_CLOSED_CONFIG` | `ai_worker/core/config.py` | `e0b5708fd2e19d7a79a87f0d61c6df4c7a0c0727677bcc4f5869445d3a69ef00` |
| `EXPLICIT_RUNTIME_ASSEMBLY` | `ai_worker/core/runtime_assembly.py` | `257af436b194d59b1d735fc73f45c1931a5855e8f66b874eb57bf3a42be12fa0` |
| `PROTECTED_ROLE_POLICY` | `infra/python/protected_retrieval_role_policy.py` | `7471c61ccd086a0e0e1da5d3f9c7ab24fae483418420a09e9ab9d46ffceffb50` |
| `ISOLATED_MIGRATION_ENV` | `infra/protected_retrieval/env.py` | `4853f8178ea611df177900573e6593c8bdc76db86a039ebd6f299013895fdbf3` |
| `ISOLATED_MIGRATION` | `infra/protected_retrieval/versions/368000000001_create_protected_retrieval.py` | `20ed57547853a39ae5254d69dd71873e0a26223a705c5231e50dc8aa7416c68d` |
| `AUTHORIZATION_CONTROL_CONTRACT` | `ai_worker/tasks/evaluation/protected_retrieval_control.py` | `4e61dc010931cee028a091e1e2f2f79daa663c91a6f4dd577b62d575ca415396` |
| `POSTGRESQL_CONTROL_ADAPTER` | `ai_worker/adapters/postgresql_protected_retrieval_control.py` | `05b6c10878215e2d79f1f65a639ea3ce091dd03c5b20f70faf4703837dbf9217` |
| `AUTHORIZATION_CONTROL_MIGRATION` | `infra/protected_retrieval/versions/368000000002_add_authorization_control.py` | `56a7275ebc83ebf69f115230e36e40bc13d0731a7c91bed900ff287b2b42c5b8` |

재생성·검증: `uv run python scripts/verify_protected_runner_evidence.py [--write]`

## 활성화 전 필수 조건

- `EXT-PRIV-001` 승인
- 실제 환경 provisioning 및 독립 Backend·Security 검증
- backup·restore·rotation 운영 증빙
- Track F external gate 충족

Evidence self hash: `d0e5e2b14c0d46368d337f60a310674a28b4aa690d32d2cc2d3fcf56b01de42e`
