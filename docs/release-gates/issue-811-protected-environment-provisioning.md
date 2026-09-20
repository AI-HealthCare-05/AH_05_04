# Issue #811 Protected Environment Provisioning & Activation Status

## 1. 개요 및 권한 경계

본 문서는 Issue #368 Protected Retrieval의 operational activation blocker인 `REAL_ENVIRONMENT_PROVISIONING`에 대한 저장소 자산(repository assets) 구현 상태와 실제 운영 환경 인계(operations handoff) 항목을 기록한다.

본 변경은 저장소 레벨의 provisioning CLI, runtime preflight, profile 격리 compose, workflow_dispatch 실행 경로를 구현한 것이며, **실제 운영 환경 프로비저닝 완료 또는 보안 상태 승격을 의미하지 않는다.**

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
| A | Protected Runner 실행 위치 | **BLOCKED_EXTERNAL_ACTION** | 저장소 내 `.github/workflows/protected_retrieval_runner.yml`은 준비되었으나, GitHub repository 상 `protected-retrieval` Environment 및 승인자가 미생성 상태임. |
| B | Protected Service Identity | **PARTIAL** | DB role policy, Alembic migration, provisioning CLI(`infra/python/provision_protected_retrieval.py`)는 완료되었으나, 운영 PostgreSQL 내 실제 로그인 계정 생성이 미수행 상태임. |
| C | Credential Injection 경로 | **BLOCKED_EXTERNAL_ACTION** | 일반 PR CI 및 개발자 환경에서는 완전히 격리(EXISTS)되어 있으나, 운영 실행용 GitHub Environment secrets 주입이 미등록 상태임. |
| D | Network / Reachability 경계 | **PARTIAL** | 운영 EC2 내 PostgreSQL 5432는 호스트 및 인터넷에 노출되지 않고 Docker `ws` 브릿지로 엄격히 격리되어 있음. GitHub-hosted runner의 직접 DB 접근은 차단되며, EC2 내부 profile one-shot 실행 경로를 통해서만 진입 가능함. |

---

## 3. 종합 준비 상태 판정

- `REPOSITORY_READY`: **READY** (Workflow, Provisioning CLI, Preflight, Compose Profile, Contract Test 전체 구현 완료)
- `ENVIRONMENT_READY`: **BLOCKED** (GitHub Environment 및 EC2 운영 배포 필요)
- `CREDENTIAL_BOUNDARY_VERIFIED`: **BLOCKED** (GitHub Environment Secret 등록 및 검증 대기)
- `NETWORK_BOUNDARY_VERIFIED`: **BLOCKED** (EC2 내부 SSH 원격 실행 및 ws 네트워크 도달 검증 대기)
- `REAL_ENVIRONMENT_PROVISIONING`: **BLOCKED** (외부 운영 조치 완료 전까지 해제 불가)

---

## 4. 운영 담당자 인계 목록 (BLOCKED_EXTERNAL_ACTION)

Repository PR 병합 후 실제 환경 프로비저닝을 위해 다음 6개 조치가 순서대로 완료되어야 한다.

### BLOCKED_EXTERNAL_ACTION 1: GitHub Environment 생성
- **담당자**: GitHub Repository Admin
- **작업 내용**:
  - GitHub 저장소 Settings → Environments에서 `protected-retrieval` Environment 생성.

### BLOCKED_EXTERNAL_ACTION 2: 독립 검토자 및 승인 정책 지정
- **担当者**: GitHub Repository Admin
- **작업 내용**:
  - `protected-retrieval` Environment에 Required reviewers 지정 (예: 권가빈, 송은영).
  - 실행 요청자 self-review 방지 옵션(`Prevent self-review`) 활성화.

### BLOCKED_EXTERNAL_ACTION 3: Environment Secrets 입력
- **담당자**: 승인된 운영 관리자
- **작업 내용**:
  - `protected-retrieval` Environment Secrets에 다음 항목을 비공개 등록:
    - 원격 접속: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `EC2_SSH_KNOWN_HOSTS`
    - 프로비저닝 전용: `DB_ADMIN_USER`, `DB_ADMIN_PASSWORD`
    - 보호 DB 스키마/역할: `PROTECTED_DB_SCHEMA`, `PROTECTED_DB_OWNER_ROLE`, `PROTECTED_DB_ACCESS_ROLE`, `PROTECTED_DB_CONTROL_ROLE`
    - 보호 로그인: `PROTECTED_DB_USER`, `PROTECTED_DB_PASSWORD`, `PROTECTED_DB_CONTROL_USER`, `PROTECTED_DB_CONTROL_PASSWORD`
    - 승인 소스: `PROTECTED_APPROVAL_REPOSITORY`, `PROTECTED_APPROVAL_BRANCH`
    - `PROTECTED_APPROVAL_GITHUB_TOKEN`은 장기 Environment PAT가 아니라 workflow의 `contents: read`, `pull-requests: read`만 명시한 job-scoped read-only `${{ github.token }}`을 주입한다. 명시하지 않은 권한은 `none`으로 유지하며, env var 이름은 유지하되 Environment Secret 등록 대상에서는 제외한다.

### BLOCKED_EXTERNAL_ACTION 4: 최신 이미지 EC2 배포
- **담당자**: 배포 담당자
- **작업 내용**:
  - #811 병합 커밋이 포함된 `app` 및 `ai-worker` 이미지를 빌드하여 운영 EC2에 배포.

### BLOCKED_EXTERNAL_ACTION 5: 원격 Provisioning One-Shot 실행
- **담당자**: 승인 reviewer 및 운영자
- **작업 내용**:
  - GitHub Actions에서 `protected_retrieval_runner.yml`을 `operation=provision`으로 `workflow_dispatch` 실행.
  - Reviewer 승인 후 EC2 원격 SSH를 통해 `protected-retrieval-provision` one-shot 컨테이너가 실행되고, Alembic head 적용, 로그인 생성, 역할 정책 적용이 완료되는지 확인.

### BLOCKED_EXTERNAL_ACTION 6: 원격 Preflight 실행 및 비민감 검증 결과 확인
- **담당자**: 승인 reviewer 및 운영자
- **작업 내용**:
  - `protected_retrieval_runner.yml`을 `operation=preflight`으로 실행.
  - 다음 비민감 JSON 출력이 정상적으로 반환되는지 확인:
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
  - 위 실행 결과가 확보된 이후에만 `REAL_ENVIRONMENT_PROVISIONING` blocker를 `CLEARED`로 전이할 수 있다.

---

## 5. 송은영(Backend/Security) 독립 검증 인계 항목

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
