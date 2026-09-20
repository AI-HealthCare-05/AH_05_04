# Issue #811 Protected Environment Provisioning & Activation Status

## 1. 개요 및 권한 경계

본 문서는 Issue #368 Protected Retrieval의 operational activation blocker인 `REAL_ENVIRONMENT_PROVISIONING`에 대한 저장소 자산(repository assets) 구현 상태와 실제 운영 환경 인계(operations handoff) 항목을 기록한다.

저장소 레벨의 provisioning CLI, runtime preflight, profile 격리 compose, workflow_dispatch 실행 경로에 더해 실제 운영 환경 provisioning 및 preflight evidence가 확보되었다. 이 evidence는 `REAL_ENVIRONMENT_PROVISIONING` blocker만 해제하며, **Protected Retrieval의 effective enforcement, access authorization, holdout/freeze, 실제 평가 실행 또는 release eligibility 승격을 의미하지 않는다.**

### 상태값 불변 확인
- `effective_enforcement_status = NOT_IMPLEMENTED`
- `access_authorized = false`
- `holdout_authored = false`
- `freeze_recorded = false`
- `actual_run_ref = null`
- `release_eligible = false`

---

## 2. 4대 미지수(Unknowns) 조사 결과

| 구분 | 영역 | 판정 | 설명 |
| --- | --- | --- | --- |
| A | Protected Runner 실행 위치 | **VERIFIED** | `protected-retrieval` Environment가 생성되었고 required reviewer 및 prevent-self-review 경계를 거쳐 실제 workflow_dispatch run이 성공했다. |
| B | Protected Service Identity | **VERIFIED** | Actual provision run `35490836570`에서 protected DB/service identity provisioning이 `PROTECTED_DATABASE_PROVISIONING_VERIFIED: PASS`로 완료되었다. |
| C | Credential Injection 경로 | **VERIFIED** | 일반 PR CI와 protected credential injection이 분리되어 있으며, approval credential은 장기 Environment PAT가 아닌 job-scoped read-only `${{ github.token }}`을 사용한다. |
| D | Network / Reachability 경계 | **VERIFIED (실행 경로 범위)** | GitHub-hosted runner에서 승인된 임시 `/32` SSH 경계를 거쳐 EC2 내부 Docker network/profile one-shot으로 provision 및 preflight가 성공했다. 이 판정은 해당 실행 경로 evidence에 한정된다. |

---

## 3. 종합 준비 상태 판정

- `REPOSITORY_READY`: **READY** (Workflow, Provisioning CLI, Preflight, Compose Profile, Contract Test 전체 구현 완료)
- `ENVIRONMENT_READY`: **READY** (Protected Environment 및 실제 protected DB/service identity provisioning 완료)
- `CREDENTIAL_BOUNDARY_VERIFIED`: **VERIFIED** (job-scoped read-only GitHub token 및 protected injection 경계 actual preflight 확인)
- `NETWORK_BOUNDARY_VERIFIED`: **VERIFIED** (승인된 임시 runner `/32` → EC2 SSH → 내부 Docker one-shot 실행 경로 확인)
- `REAL_ENVIRONMENT_PROVISIONING`: **CLEARED** (actual provision PASS 및 actual preflight READY evidence 확보)

---

## 4. 완료된 운영 조치

다음 운영 조치는 protected workflow 경계를 통해 완료되었다.

1. **GitHub Environment 생성 — COMPLETED**
   - `protected-retrieval` Environment가 생성되었다.
2. **Required reviewer / self-review prevention — COMPLETED**
   - Required reviewer 및 prevent-self-review 경계를 사용해 actual run을 승인했다.
3. **Protected secrets/config injection — COMPLETED**
   - 원격 접속, protected DB role/login 및 approval repository/branch 설정이 protected Environment 경계로 주입되었다.
   - 장기 `PROTECTED_APPROVAL_GITHUB_TOKEN` Environment Secret은 제거되었고, 동일 env var에는 job-scoped `${{ github.token }}`이 주입된다.
4. **Required runtime image availability — COMPLETED**
   - Actual provision 및 preflight one-shot이 필요한 runtime image로 실행되었다.
5. **Provision one-shot — PASS**
   - Run `35490836570`에서 `PROTECTED_DATABASE_PROVISIONING_VERIFIED: PASS`를 확인했다.
6. **Runtime preflight — READY**
   - Run `35493100169`에서 protected runtime 결과 7개가 모두 PASS/READY로 확인되었다.

---

## 5. Actual Evidence

### Provision

- Workflow run: [`35490836570`](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35490836570)
- Head SHA: `b293a2c26e00cb7120a5a009d4a116bbd4a04887`
- Result: `PROTECTED_DATABASE_PROVISIONING_VERIFIED: PASS`

### Preflight

- Workflow run: [`35493100169`](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35493100169)
- Head SHA: `14c3389dbb6120b584750057c816772a084b54d0`
- Non-sensitive result:

  ```json
  {
    "config_boundary": "PASS",
    "data_connection": "PASS",
    "control_connection": "PASS",
    "approval_repository_read": "PASS",
    "approval_branch_read": "PASS",
    "approval_token_write_boundary": "PASS",
    "protected_runtime": "READY"
  }
  ```

### Approval credential

- Source: job-scoped `${{ github.token }}`
- Workflow permissions:
  - `contents: read`
  - `pull-requests: read`
- 그 외 GitHub permissions는 부여하지 않는다.
- 장기 `PROTECTED_APPROVAL_GITHUB_TOKEN` Environment Secret은 제거되었다.
- Token 값은 evidence에 기록하지 않는다.

### State transition

- `REAL_ENVIRONMENT_PROVISIONING`: **BLOCKED → CLEARED**
- 이 전이는 #811이 담당한 실제 환경 provisioning blocker에만 적용된다.

### Remaining blockers

- `EXT_PRIV_001`
- `INDEPENDENT_BACKEND_SECURITY_VERIFICATION`
- `BACKUP_RESTORE_AND_ROTATION_EVIDENCE`
- `TRACK_F_EXTERNAL_GATE`

---

## 6. 송은영(Backend/Security) 독립 검증 인계 항목

`REAL_ENVIRONMENT_PROVISIONING` evidence pack은 독립 Backend/Security 검증을 위해 준비되었다. 이 문서는 `INDEPENDENT_BACKEND_SECURITY_VERIFICATION` blocker를 해제하지 않는다. 검토 대상 actual run은 provision `35490836570`과 preflight `35493100169`이다.

1. `infra/python/provision_protected_retrieval.py`의 최소 권한 및 롤 분리 로직 검증:
   - `PROTECTED_DB_USER`(DATA)는 `ACCESS_ROLE`만 소유하며, `CONTROL_ROLE` 멤버십이 차단됨.
   - `PROTECTED_DB_CONTROL_USER`(CONTROL)는 `CONTROL_ROLE`만 소유하며, `ACCESS_ROLE` 멤버십이 차단됨.
   - 일반 `DB_APP_USER`와의 로그인/권한 충돌 방지 로직 확인.
2. `infra/docker/docker-compose.prod.yml`의 one-shot 프로파일 격리 검증:
   - `protected-retrieval-admin` 프로파일과 `protected-runner` 프로파일의 credentials가 상호 엄격히 격리되어 있음.
   - 일반 `ai-worker` 및 `fastapi` 서비스에 어떠한 protected credentials도 전달되지 않음.
   - 포트 매핑 없음(`ports` 부재) 및 내부 Docker 네트워크 `ws` 사용 확인.
3. `.github/workflows/protected_retrieval_runner.yml`의 비민감성 및 원격 실행 제어 검증:
   - ephemeral env 파일의 umask 077 및 실행 즉시 rm 삭제(trap cleanup) 보장.
   - `EC2_SSH_KNOWN_HOSTS` 기반 host key pinning 및 `StrictHostKeyChecking=yes` 강제.
