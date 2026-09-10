# Product Decision Candidate: Protected Retrieval Runner 접근 통제·감사 경계

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-368-20260909` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-09 |
| 구현 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Privacy·Safety·Evaluation |
| Dataset Custodian·Backend·Security 검토 | 송은영 (`@phina-io`) — 송은영이 실제 접근 통제를 구현하면 독립 Dataset Custodian 승인은 김지혜(`@Jye-rookie`)가 담당 |
| 추적 Issue | [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) (상위 #273) |
| 관련 PR | PR #373 (kernel 구현), PR #366 |

## 1. 목적·배경

HOLDOUT Dataset(질문·Gold·hard negative)을 일반 개발·CI 접근으로부터 격리하고, 승인·역할·감사 경계 없이 실행 표면이 먼저 열리는 것을 막는다. PR #373은 인프라 독립 security kernel과 synthetic adapter만 구현했고, 실제 저장 인프라는 이번 결정 이후 후속 PR에서 연결한다.

## 2. 범위 (Scope / Out-of-scope)

포함: PostgreSQL DB/schema 격리, Owner/Author/Custodian/Runner role과 deny 정책, Credential 주입 환경, Audit 보존 기간, Backup/revoke/incident-response/재검토 주기.

제외: 실제 HOLDOUT 작성·Freeze·실행, `run-protected-holdout` 구현, Retrieval/Metric/Release. 승인 전에는 PostgreSQL migration, secret, protected path를 구현하지 않는다.

## 3. DB/Schema 격리

Protected 전용 **schema**로 확정한다(별도 database 아님). 구현은 이 문서 PR이 아니라 **후속 infrastructure adapter PR**에서 하되, #368의 effective enforcement 선행조건으로 명시한다.

구현 시 아래 항목을 실제 SQL negative test로 검증한다. ①~③은 기존 provisioning 스크립트가 `public` schema·`app_user`에 이미 적용 중인 참고 패턴이고, ④~⑤가 **protected schema 자체에 적용해야 하는 deny 조건**이다.

- ① 전용 role 분리: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`로 관리 권한 없는 role 생성
- ② (참고 패턴) `public` schema 권한 기본 회수: `REVOKE CREATE ON SCHEMA public FROM app_user`
- ③ (참고 패턴) default privilege로 동일 거부 정책 유지: `ALTER DEFAULT PRIVILEGES FOR ROLE migration_user ... REVOKE UPDATE ON SEQUENCES`, 승인된 함수에만 `GRANT EXECUTE`
- ④ (신규, protected schema 대상) `PUBLIC`과 일반 `app_user`(이 저장소는 CI도 별도 role 없이 `app_user`/`migration_user`를 그대로 쓰므로 CI 접근도 이걸로 함께 차단됨)의 schema 권한(`USAGE`/`CREATE`)과 **기존·향후 모든 table/sequence/function 권한**을 명시적으로 거부. 함수의 기본 `PUBLIC EXECUTE`도 회수 대상에 포함하고, protected owner 기준 `ALTER DEFAULT PRIVILEGES`로 향후 생성 객체에도 동일 적용
- ⑤ (신규) 위 거부 조건을 `\dn+`/`\dp`로 실제 권한을 조회해 SQL negative test로 검증 — PostgreSQL이 커스텀 스키마에 PUBLIC 권한을 기본 부여하지 않아 일부가 이미 no-op일 수 있으므로, 실제 상태를 먼저 확인한 뒤 필요한 REVOKE만 남긴다

## 4. 역할·책임 + 직무 분리 (Segregation of Duties)

PR #373 kernel이 이미 구현한 3개 역할을 그대로 사용한다.
- `HOLDOUT_AUTHOR`: 질문·Gold 작성 (정현우)
- `DATASET_CUSTODIAN`: 검토·Freeze (송은영)
- `PROTECTED_RUNNER`: Freeze된 Dataset 읽기·실행 (승인된 service identity)
- 송은영이 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜로 전환
- Author/Runner grant issuer는 subject 및 control implementation participant와 달라야 함(self-approval 차단)
- 송은영 본인의 READ/FREEZE grant는 권가빈이 `PRODUCT_SAFETY_REVIEWER` 역할로 독립 발급

여기에 더해, 실제 DB 인프라 레벨에서 "protected owner/migration role"(DB 객체 소유·migration 전용, `NOLOGIN`)을 신설한다 — kernel role과 충돌하지 않는 별도 layer이며, 기존 Runtime 권한 분리 원칙과 정합해야 한다.

`HOLDOUT_AUTHOR`의 WRITE, `DATASET_CUSTODIAN`의 READ, `PROTECTED_RUNNER`의 READ 같은 실제 데이터 접근은 §3에서 전면 거부한 일반 `app_user`도, DDL·migration 전용인 protected owner/migration role도 아닌 별도의 **`protected_access_role`**(`NOLOGIN` 그룹 role, 필요한 최소 DML 권한만 보유. kernel의 `PROTECTED_RUNNER` 원칙과는 다른 개념이므로 이름을 구분함)을 통해 이뤄진다. 개별 identity(사람 계정 또는 Runner service identity)는 이 그룹 role의 멤버로만 접근하고, 공유 login credential을 직접 쓰지 않는다.

"approval role"은 `ProtectedApprovalRole`(kernel의 governance/application 계층)로 유지하고, 별도의 공유 PostgreSQL login role로 중복 구현하지 않는다. 승인자는 개별 identity로 식별되고 approval evidence가 검증되어야 한다. DB 권한이 추가로 필요한지는 Backend·Security 구현 설계 단계에서 판단한다.

`FREEZE`·`RUN`·grant/revoke의 기본 경계는 `CONTRIBUTING.md`의 "DB 내부의 암묵적 동작보다 Application Service에서 명시적으로 추적할 수 있는 로직을 우선한다" 원칙에 따라 **Application Service**로 둔다.

### #398 / PR #429에서 대체한 Source 선례

이 절의 기존 SECURITY DEFINER 예외 제안은 `165e8f706152`와 [과거 Source DB 전이 결정](./2026-09-08-source-snapshot-db-transition.md)을 승인 선례로 인용했다. 해당 Source 결정은 #398의 Python 전환으로 **superseded**되었으므로 현재 구현 근거로 사용할 수 없다. 함수 owner·search_path·EXECUTE 인계에 관한 과거 제안도 신규 저장 함수 도입 지침으로 사용하지 않는다.

현재 `CONTRIBUTING.md`·`AGENTS.md`에 따라 DB Trigger·RLS·업무 저장 함수를 추가하지 않는다. `FREEZE`·`RUN`·grant/revoke는 명시적 Python Service/Repository transaction에서 검증하고, 실행 역할·credential 분리와 직접 DML 회수, 일반 FK·UNIQUE·CHECK로 뒷받침해야 한다. 동적 SQL/다른 실행 경로가 Python 검사를 우회할 수 있다는 위험은 남으므로 권한 경계를 실제 제한 로그인·동시성·rollback 통합 테스트로 입증해야 한다. 정적 검색만으로 raw SQL 우회를 막았다고 판정하지 않는다.

이 정렬은 protected schema 전체 ACL이나 Runner 기능의 구현·승인을 완료하는 변경이 아니다. 실제 구현 권한 목록과 관리 경로는 Source 선례를 자동 복제하지 않고 기존 담당 DB·Security·RAG 리뷰 경로로 확정한다. 대체 Decision은 [PD-398-R1](./2026-09-10-python-integrity-review-429.md)이다.

사람(Author·Custodian)과 Runner는 공유 login이 아니라 개별 identity + 단기 credential이어야 한다 — 공유 계정을 쓰면 §7 audit가 "누가 접근했는가"를 실제로 답할 수 없게 된다.

## 5. 접근 통제 모델

Least privilege, default-deny. 표에 없는 역할·action·상태 조합은 항상 거부한다.

| 역할·작업 | 허용 상태와 필수 evidence |
| --- | --- |
| `HOLDOUT_AUTHOR / READ·WRITE` | `ACCESS_AUTHORIZED` 또는 `AUTHORING`, unfrozen, current authorization revision |
| `DATASET_CUSTODIAN / READ` | 4개 상태 중 하나, 독립 발급된 Custodian access grant |
| `DATASET_CUSTODIAN / FREEZE` | `REVIEW_READY`, authored 40, 전수 검토, 네 leakage 축 0 evidence |
| `PROTECTED_RUNNER / READ·RUN` | `FROZEN`, Freeze Receipt, execution authorization, #178 Adapter·Index·config binding |

## 6. Credential/Secret 관리

LOCAL 환경만 placeholder 허용, 그 외 환경은 실값 강제 + fail-closed validator(기동 시점 `ValueError`)를 protected credential에도 적용한다. credential은 repository/`.env` 미저장, 실행 시점 단기 주입, 종료 후 폐기/회전, `workflow_dispatch` 수동 실행 + required reviewer.

Authoring 환경은 아직 확정되지 않았다. 사용할 환경의 **소유자, 접근 방식, 로그·artifact 비노출, credential 회전 방법**이 구체적으로 확인될 때까지 authoring 승인은 차단 상태로 유지한다.

기존 요구사항은 막연한 "2인 승인"이 아니라 실행 요청자와 분리된 독립 승인자 1인의 승인이다. 2인 승인을 새 요구사항으로 만들려면 별도 합의가 필요하다. GitHub Environment의 self-review 방지 기능을 사용하되, 승인 가능한 목록이 지정된 독립 승인자로 제한되어 있는지와 실행 요청자의 self-review가 실제로 차단되는지를 함께 검증한다.

접근 통제와 별개로, 아래 조건도 필요하다 — 접근을 아무리 잘 막아도 이게 없으면 artifact로 정답이 새어 평가 자체가 무효화된다.
- 승인은 특정 commit과 Dataset manifest hash에 고정되어야 한다(승인 당시와 다른 commit/hash로 실행되지 않도록)
- 질문·Gold·hard negative는 workflow input, 로그, cache, artifact에 남지 않도록 차단되어야 한다

## 7. 감사·로깅

PR #373 — append-only hash-chain journal(synthetic, global monotonic sequence, tamper 검증). 단 정책 검증용이며 실제 durable retention을 증명하지 않는다.

실제 인프라에 아래가 별도로 필요하며, §3과 동일하게 후속 infrastructure adapter PR에서 구현한다.
- DB 수준 UPDATE/DELETE/TRUNCATE 차단
- global sequence + durable head/checkpoint
- backup·restore 검증

Authorization·revoke·Freeze·run audit evidence는 각 event 생성 시점 기준 **최소 1년**을 별도로 보장하고, legal hold가 있으면 해제 시까지 보존한다. 단 이는 기존 저장소 정책의 재사용이 아니라 신규 정책안이며, 실제 적용은 [`docs/deployment.md:285`](../../deployment.md)의 **`EXT-PRIV-001`** 승인(정책·승인 인수: 권가빈, 기술 증빙: 송은영)과 보관 위치가 확정된 뒤에만 가능하다. 보관 위치와 기술적 삭제·복구 방식은 Backend·Security 담당자(송은영)가 제시한다.

보존기간의 기산점으로 Freeze 시점을 쓰지 않는다 — Dataset은 Freeze 이후에도 계속 운영·평가에 쓰일 수 있어, Freeze 기준 시간 경과만으로는 아직 사용 중인 audit evidence까지 폐기 대상이 될 수 있다. 대신 Dataset version의 명시적인 **운영 종료·폐기 가능 상태**(판정 가능한 lifecycle marker)를 후속 계약으로 정의하고, 그 상태가 구현되기 전까지는 시간 경과만으로 자동 폐기하지 않는다.

## 8. 보존·폐기 정책

Dataset 원본(질문·Gold 본문)의 폐기도 §7과 동일하게 **Dataset version의 명시적인 운영 종료·폐기 가능 상태**가 정의·구현되기 전까지는 하지 않는다. 그 상태가 구현된 뒤에는 Custodian의 폐기 요청 + Product·Evaluation 책임자(권가빈)의 독립 승인을 거친다.

현재 kernel enum(`READ|WRITE|FREEZE|RUN`, `GRANT|REVOKE|EXPIRE`, `DENIED|INTENT|SUCCEEDED|UNKNOWN`)에 폐기 이벤트를 억지로 끼워 넣지 않는다. 대신 삭제 전 `INTENT`(Dataset version/digest, 승인, legal hold·backup 조건 결속) → 삭제 후 `SUCCEEDED`/`UNKNOWN` → 재조정 절차를 갖는 별도 disposal audit 계약으로 분리한다. 이는 공유 계약 변경이므로 이 문서와 별도로 계약·구현·테스트가 필요하며, [#425](https://github.com/AI-HealthCare-05/AH_05_04/issues/425)에서 추적한다.

Freeze Receipt와 manifest hash 대조는 Dataset·실행 입력의 identity와 무결성만 확인할 수 있고, 과거 평가 결과를 재현하지는 못한다 — 원본 폐기 후에는 재실행 기반 재현이 불가능하며, 가능한 건 provenance·integrity 확인뿐이다. "원본 재실행을 요구하지 않는다"는 이 문서의 합의 사항이 아니라 별도의 Product·Evaluation 정책 결정이 필요한 사항이며, 그 결정 전까지는 폐기와 재현 요구가 충돌할 수 있는 상태로 남는다.

## 9. 백업·복구

Mutable authoring 기간에는 일 단위 backup, Freeze 및 version 변경 시점에는 별도 snapshot, 분기별 restore test를 기본안으로 한다.

보관 위치와 기술적 방식은 **Backend·Security(송은영) 설계에서 확정**한다. Custodian은 비민감 검증 evidence만 확인한다.

기존 `pg_dump -Fc` 백업(`scripts/deployment.sh:588-591`)을 그대로 재사용하는 것으로 확정하지 않는다 — 그 덤프는 admin 계정이 전체 DB를 읽어 일반 `deployment-evidence`에 저장하므로, protected schema를 같은 덤프에 포함하면 일반 배포·백업 경로가 §3~§5의 접근 통제를 우회하는 새로운 HOLDOUT 노출 경로가 된다. 아래가 선행 조건으로 확인되기 전까지는 기존 메커니즘 재사용 여부를 결정하지 않는다.

- 암호화
- protected 전용 ACL(일반 배포·백업 계정과 분리)
- 일반 CI·배포 artifact에 비노출
- credential 분리
- 보존·삭제 정책과의 연계
- 격리된 restore test(위 조건 위에서 검증) — `pg_restore` 사용례가 저장소에 없으므로(0건) 이 절차 자체도 새로 만들어야 한다

## 10. 정기 접근 검토 주기

실행 전 유효성 검증 + 최대 분기별 정기 검토. 기술적 검토·revoke 실행은 Backend·Security(송은영) 담당, 독립 정책 검토는 Product·Privacy(권가빈) 담당으로 분리한다. 저장소에는 identity나 보호 위치가 포함되지 않은 검토 receipt만 남긴다.

## 11. 사고 대응 절차 (Incident Response)

사고 대응 절차는 아래 7단계로 한다.
1. authoring과 Runner 실행 중단
2. 관련 grant revoke 및 credential 회전
3. audit와 관련 증빙 보존
4. 영향받은 Freeze Receipt와 실행 승인 무효화
5. 접근·노출 범위 조사
6. 필요한 경우 Dataset 재검토·재작성·재Freeze
7. 독립 Custodian 재승인 후 실행 재개

5단계(조사)는 다음과 같이 분리한다.
- **Privacy 영향 판단**: 권가빈이 담당
- **Security 기술 조사**: 원칙적으로 Backend·Security 담당자(송은영)가 수행하되, 송은영이 통제 구현자이거나 사고 당사자인 경우엔 그때 명시적으로 지정한 독립 Security 조사자가 맡는다. 김지혜를 별도 합의 없이 자동 대체 조사자로 고정 지정하지 않고, 매 사고마다 별도 합의로 지정한다.

상세 사고 자료는 비공개 보관하고, 저장소에는 비민감 요약 evidence만 남긴다.

## 12. 예외 처리 절차

HOLDOUT은 긴급 진료 데이터가 아니므로 break-glass 예외는 두지 않고 표준 grant/revoke 절차만 허용한다. 근거는 "HOLDOUT은 합성 데이터라 긴급성이 없다"는 판단이다.

## 13. 문서 재검토 주기·만료일

infrastructure adapter 연결 PR, 역할·환경·정책 변경 시 재검토하고, 변경이 없더라도 연 1회 정기 재검토를 병행한다.

## 14. 관련 문서·참조

- [Issue #368 Security Kernel 설계](../../designs/ceohwj/issue-368-protected-retrieval-runner-security-kernel-design.md)
- `ai_worker/tasks/evaluation/protected_retrieval.py` — kernel 본체(정책, 인프라 독립)
- `ai_worker/tasks/evaluation/protected_retrieval_synthetic.py` — synthetic in-memory adapter
- `docs/validation/rag/issue-273/protected-runner-foundation.md`
- `infra/docker/postgres/configure-app-role.sql` — §3 role/권한 provisioning 선례
- [PD-398-R1 Python 무결성 리뷰 보완](./2026-09-10-python-integrity-review-429.md) — §4의 현재 구현 원칙. `165e8f706152`와 [과거 Source DB 전이 결정](./2026-09-08-source-snapshot-db-transition.md)은 superseded된 이력이며 신규 함수 선례가 아니다.
- `CONTRIBUTING.md` — §4 DB 함수 도입 예외 5개 항목 기준
- `backend/app/core/config.py` — §6 credential fail-closed validator 패턴
- `.github/workflows/checks.yml` — §6 GitHub Actions 현황 확인
- `docs/privacy-safety.md` — §7 보존기간(다른 값, 전부 미적용), §12 예외 미허용 기조
- [`docs/deployment.md:285`](../../deployment.md) — §7 `EXT-PRIV-001` 승인 게이트
- `docs/contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md` — §8 삭제 전 INTENT 기록 선례
- [#425](https://github.com/AI-HealthCare-05/AH_05_04/issues/425) — §8 disposal audit 계약 후속 Issue
- `scripts/deployment.sh` — §9 검토 대상 기존 `pg_dump` 백업 메커니즘(재사용 여부 미확정, 선행조건 검토 중)
- `docs/runbooks/` — §11 참고(장애 복구용, IR 프레이밍 아님)
- `docs/validation/rag/issue-273/` — §11 사고 증빙 위치

## 완료 후 상태 (현재)

policy foundation: `IMPLEMENTED` / effective enforcement: `NOT_IMPLEMENTED` / infrastructure adapter: `NOT_IMPLEMENTED` / HOLDOUT access authorization: `NOT_RECORDED` / HOLDOUT authored: `0` / HOLDOUT Freeze: `NOT_STARTED` — 위 결정은 방향 합의일 뿐이며, 실제 인프라·보관 위치·credential·SQL 통제가 구현·테스트되기 전까지 #368은 Open, `effective_enforcement_status=NOT_IMPLEMENTED`, HOLDOUT 미승인 상태를 유지한다.
