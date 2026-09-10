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

구현 시 아래 4가지를 실제 SQL 테스트로 검증한다. ①~③은 기존 provisioning 스크립트가 이미 적용 중인 패턴을 protected schema 대상으로 확장하는 것이고, ④만 이번에 새로 추가하는 항목이다.

- ① 전용 role 분리: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`로 관리 권한 없는 role 생성
- ② 스키마 권한 기본 회수: `REVOKE CREATE ON SCHEMA public FROM app_user`
- ③ default privilege로 동일 거부 정책 유지: `ALTER DEFAULT PRIVILEGES FOR ROLE migration_user ... REVOKE UPDATE ON SEQUENCES`, 승인된 함수에만 `GRANT EXECUTE`
- ④ (신규) `REVOKE ALL ON SCHEMA <protected> FROM PUBLIC`

## 4. 역할·책임 + 직무 분리 (Segregation of Duties)

PR #373 kernel이 이미 구현한 3개 역할을 그대로 사용한다.
- `HOLDOUT_AUTHOR`: 질문·Gold 작성 (정현우)
- `DATASET_CUSTODIAN`: 검토·Freeze (송은영)
- `PROTECTED_RUNNER`: Freeze된 Dataset 읽기·실행 (승인된 service identity)
- 송은영이 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜로 전환
- Author/Runner grant issuer는 subject 및 control implementation participant와 달라야 함(self-approval 차단)
- 송은영 본인의 READ/FREEZE grant는 권가빈이 `PRODUCT_SAFETY_REVIEWER` 역할로 독립 발급

여기에 더해, 실제 DB 인프라 레벨에서 "protected owner/migration role"(DB 객체 소유·migration 전용, `NOLOGIN`)을 신설한다 — kernel role과 충돌하지 않는 별도 layer이며, 기존 Runtime 권한 분리 원칙과 정합해야 한다.

"approval role"은 `ProtectedApprovalRole`(kernel의 governance/application 계층)로 유지하고, 별도의 공유 PostgreSQL login role로 중복 구현하지 않는다. 승인자는 개별 identity로 식별되고 approval evidence가 검증되어야 한다. DB 권한이 추가로 필요한지는 Backend·Security 구현 설계 단계에서 판단한다.

`FREEZE`·`RUN`·grant/revoke는 직접 테이블 DML로 허용하지 않고, Dataset 상태와 승인 evidence를 검증하는 제한된 함수 경계로만 수행한다 — 기존 provisioning 스크립트가 이미 쓰고 있는 SECURITY DEFINER 함수(승인된 함수에만 `GRANT EXECUTE`)와 같은 구조다.

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

기존 요구사항은 막연한 "2인 승인"이 아니라 실행 요청자와 분리된 독립 승인자 1인의 승인이다. 2인 승인을 새 요구사항으로 만들려면 별도 합의가 필요하다. 우선 GitHub Environment의 self-review 방지 + 지정된 독립 승인자 1인의 approval evidence를 함께 검증하는 방향으로 정리한다. (GitHub required reviewer는 여러 명을 등록해도 그중 한 명만 승인하면 진행되므로, "독립 승인자 1인" 요건을 만족하려면 승인자 목록을 정확히 1인 이상으로 지정하고 self-review 방지가 실제로 작동하는지 별도 검증이 필요하다.)

접근 통제와 별개로, 아래 조건도 필요하다 — 접근을 아무리 잘 막아도 이게 없으면 artifact로 정답이 새어 평가 자체가 무효화된다.
- 승인은 특정 commit과 Dataset manifest hash에 고정되어야 한다(승인 당시와 다른 commit/hash로 실행되지 않도록)
- 질문·Gold·hard negative는 workflow input, 로그, cache, artifact에 남지 않도록 차단되어야 한다

## 7. 감사·로깅

PR #373 — append-only hash-chain journal(synthetic, global monotonic sequence, tamper 검증). 단 정책 검증용이며 실제 durable retention을 증명하지 않는다.

실제 인프라에 아래가 별도로 필요하며, §3과 동일하게 후속 infrastructure adapter PR에서 구현한다.
- DB 수준 UPDATE/DELETE/TRUNCATE 차단
- global sequence + durable head/checkpoint
- backup·restore 검증

Authorization·revoke·Freeze·run audit evidence는 해당 Dataset version의 마지막 Freeze Receipt 생성일 기준 최소 1년, legal hold가 있으면 해제 시까지 보존한다(기산점을 "운영 종료 후"로 두면 시스템이 판정할 수 없어, kernel이 기록하는 Freeze 시점으로 대체). 단 이는 기존 저장소 정책의 재사용이 아니라 신규 정책안이며, 실제 적용은 Privacy·필요한 외부 승인과 보관 위치가 확정된 뒤에만 가능하다. 보관 위치와 기술적 삭제·복구 방식은 Backend·Security 담당자(송은영)가 제시한다.

## 8. 보존·폐기 정책

Dataset 원본(질문·Gold 본문)도 §7과 동일하게 마지막 Freeze Receipt 생성일 기준 1년을 기본안으로 하되, Custodian의 폐기 요청 + Product·Evaluation 책임자(권가빈)의 독립 승인을 거친다.

현재 kernel enum(`READ|WRITE|FREEZE|RUN`, `GRANT|REVOKE|EXPIRE`, `DENIED|INTENT|SUCCEEDED|UNKNOWN`)에 폐기 이벤트를 억지로 끼워 넣지 않는다. 대신 삭제 전 `INTENT`(Dataset version/digest, 승인, legal hold·backup 조건 결속) → 삭제 후 `SUCCEEDED`/`UNKNOWN` → 재조정 절차를 갖는 별도 disposal audit 계약으로 분리한다. 이는 공유 계약 변경이므로 이 문서와 별도로 계약·구현·테스트가 필요하다(이번 PR 범위 밖, 후속 작업으로 이관).

폐기 요구와 "과거 평가를 재현해달라"는 요구가 나중에 충돌하지 않도록, 과거 평가의 재현 검증은 Freeze Receipt와 manifest hash 대조로만 하고 원본(Dataset 실제 내용) 재실행을 요구하지 않는다는 경계도 같이 고정한다.

## 9. 백업·복구

Mutable authoring 기간에는 일 단위 backup, Freeze 및 version 변경 시점에는 별도 snapshot, 분기별 restore test를 기본안으로 한다.

보관 위치와 기술적 방식은 기존 `pg_dump -Fc` 메커니즘(`scripts/deployment.sh:570-576`, migration 배포 시마다 실행)을 재사용한다 — protected schema도 같은 DB 인스턴스 안에 있으므로 별도 인프라가 필요 없다. 다만 이 메커니즘은 migration/배포 시점에만 실행되므로, "authoring 기간 일 단위 backup"은 같은 명령을 별도로 스케줄링(cron 등)해야 하고, "Freeze 시점 snapshot"은 Freeze 시점에 같은 명령을 한 번 더 실행하는 것으로 충족한다. 복구는 `pg_restore`.

암호화·접근 통제 세부사항과 실행 담당자는 Backend·Security(송은영)가 확정하고, Custodian은 비민감 검증 evidence만 확인한다.

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
- `docs/validation/rag/issue-273/protected-runner-foundation.md`
- `infra/docker/postgres/configure-app-role.sql` — §3·§4 role/권한 provisioning 선례
- [Source Snapshot DB 상태 전이 결정](./2026-09-08-source-snapshot-db-transition.md) — §4 protected owner role 설계 참고
- `backend/app/core/config.py` — §6 credential fail-closed validator 패턴
- `.github/workflows/checks.yml` — §6 GitHub Actions 현황 확인
- `docs/privacy-safety.md` — §7 보존기간(다른 값, 전부 미적용), §12 예외 미허용 기조
- `docs/contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md` — §8 삭제 전 INTENT 기록 선례
- `scripts/deployment.sh` — §9 기존 `pg_dump` 백업 메커니즘
- `docs/runbooks/` — §11 참고(장애 복구용, IR 프레이밍 아님)
- `docs/validation/rag/issue-273/` — §11 사고 증빙 위치

## 완료 후 상태 (현재)

policy foundation: `IMPLEMENTED` / effective enforcement: `NOT_IMPLEMENTED` / infrastructure adapter: `NOT_IMPLEMENTED` / HOLDOUT access authorization: `NOT_RECORDED` / HOLDOUT authored: `0` / HOLDOUT Freeze: `NOT_STARTED` — 위 결정은 방향 합의일 뿐이며, 실제 인프라·보관 위치·credential·SQL 통제가 구현·테스트되기 전까지 #368은 Open, `effective_enforcement_status=NOT_IMPLEMENTED`, HOLDOUT 미승인 상태를 유지한다.
