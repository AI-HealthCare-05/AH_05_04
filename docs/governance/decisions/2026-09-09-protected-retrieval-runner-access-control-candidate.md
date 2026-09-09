# Product Decision Candidate: Protected Retrieval Runner 접근 통제·감사 경계

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-368-20260909` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-09 |
| 구현 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Privacy·Safety·Evaluation |
| Dataset Custodian·Backend·Security 검토 | 송은영 (`@phina-io`) — 송은영이 실제 접근 통제를 구현하면 독립 Dataset Custodian 승인은 김지혜(`@Jye-rookie`)가 담당 |
| 선행 Decision | [Source Snapshot DB 상태 전이](./2026-09-08-source-snapshot-db-transition.md) — protected DB-owned 연산(SECURITY DEFINER + PUBLIC EXECUTE 회수) 선례 |
| 추적 Issue | [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) (상위 #273) |
| 관련 PR | PR #373 (kernel 구현), PR #366 |

관련 설계: [Issue #368 Security Kernel 설계](../../designs/ceohwj/issue-368-protected-retrieval-runner-security-kernel-design.md)

아래 각 절은 **확정**(가빈 2026-09-09 코멘트로 방향 결정됨)/**채택(안)**(저장소 관행을 그대로 따르는 부분)과 **협의 필요**(아직 답이 없는 질문)를 구분한다. "**참고용 제안**"으로 표시된 항목은 의료 분야 일반 관행을 참고해 제시한 것일 뿐 이 프로젝트에 대한 확정 근거가 아니며, 실제 채택은 별도 승인이 필요하다(가빈 요청: 확정 사실처럼 보이지 않게 표시).

## 1. 목적·배경

HOLDOUT Dataset(질문·Gold·hard negative)을 일반 개발·CI 접근으로부터 격리하고, 승인·역할·감사 경계 없이 실행 표면이 먼저 열리는 것을 막는다. PR #373은 인프라 독립 security kernel과 synthetic adapter만 구현했고, 실제 저장 인프라는 이번 결정 이후 후속 PR에서 연결한다.

## 2. 범위 (Scope / Out-of-scope)

포함: PostgreSQL DB/schema 격리, Owner/Author/Custodian/Runner role과 deny 정책, Credential 주입 환경, Audit 보존 기간, Backup/revoke/incident-response/재검토 주기.

제외: 실제 HOLDOUT 작성·Freeze·실행, `run-protected-holdout` 구현, Retrieval/Metric/Release. 승인 전에는 PostgreSQL migration, secret, protected path를 구현하지 않는다.

## 3. DB/Schema 격리

**확정(가빈)**: protected 전용 **schema**로 확정(별도 database 아님). PUBLIC 권한은 **명시적으로 전부 회수**해야 하며, 일반 app·CI 접근 거부와 default privilege까지 **실제 SQL 테스트로 검증**해야 한다. 구현은 **이 문서 PR이 아니라 후속 infrastructure adapter PR**에서 하되, **#368의 effective enforcement 선행조건**으로 명시한다.

**채택(안) — 이미 구현된 동일 패턴 확장**: 아래 ①~③은 새로 설계할 게 아니라 [`infra/docker/postgres/configure-app-role.sql`](../../../infra/docker/postgres/configure-app-role.sql)이 `migration_user`/`app_user`에 이미 적용 중인 패턴을 protected schema 대상으로 확장하는 것이다.
- ① 전용 role 분리: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`로 관리 권한 없는 role 생성 ([:24-30](../../../infra/docker/postgres/configure-app-role.sql), [:60-66](../../../infra/docker/postgres/configure-app-role.sql))
- ② 스키마 권한 기본 회수: `REVOKE CREATE ON SCHEMA public FROM app_user` ([:81-85](../../../infra/docker/postgres/configure-app-role.sql))
- ③ default privilege로 동일 거부 정책 유지: `ALTER DEFAULT PRIVILEGES FOR ROLE migration_user ... REVOKE UPDATE ON SEQUENCES FROM app_user` ([:132-138](../../../infra/docker/postgres/configure-app-role.sql)), 승인된 함수에만 `GRANT EXECUTE` ([:97-103](../../../infra/docker/postgres/configure-app-role.sql))
- ④ (확정된 신규 항목) `REVOKE ALL ON SCHEMA <protected> FROM PUBLIC` — `configure-app-role.sql`에 없던 것을 이번에 protected schema용으로 새로 추가

[Source Snapshot DB 상태 전이 결정](./2026-09-08-source-snapshot-db-transition.md)도 같은 저장소에서 SECURITY DEFINER 함수 + PUBLIC EXECUTE 회수로 protected DB-owned 연산을 이미 다룬 선례다.

## 4. 역할·책임 + 직무 분리 (Segregation of Duties)

**채택(안)**: PR #373 kernel이 이미 구현한 3개 역할을 그대로 사용한다.
- `HOLDOUT_AUTHOR`: 질문·Gold 작성 (제안: 정현우)
- `DATASET_CUSTODIAN`: 검토·Freeze (제안: 송은영)
- `PROTECTED_RUNNER`: Freeze된 Dataset 읽기·실행 (승인된 service identity)
- 송은영이 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜로 전환
- Author/Runner grant issuer는 subject 및 control implementation participant와 달라야 함(self-approval 차단, kernel에 이미 구현됨)
- 송은영 본인의 READ/FREEZE grant는 권가빈이 `PRODUCT_SAFETY_REVIEWER` 역할로 독립 발급 (kernel `ProtectedApprovalRole`에 이미 존재)

여기에 더해, 실제 DB 인프라 레벨에서 **"protected owner/migration role"**(DB 객체 소유·migration 전용, `NOLOGIN`)을 신설 — 이건 kernel role과 충돌하지 않는 별도 layer이므로 이견 없이 채택. 이 role 설계는 [Source Snapshot DB 상태 전이 결정](./2026-09-08-source-snapshot-db-transition.md)의 "Runtime은 owner/superuser 또는 owner 역할의 멤버로 운영하지 않는다" 원칙과 정합해야 한다.

**확정(가빈)**: 현우가 제안한 "approval role"은 `ProtectedApprovalRole`(kernel의 governance/application 계층)로 유지하고, **별도의 공유 PostgreSQL login role로 중복 구현하지 않는다.** 승인자는 개별 identity로 식별되고 approval evidence가 검증되어야 한다. DB 권한이 추가로 필요한지는 Backend·Security 구현 설계 단계에서 판단한다.

## 5. 접근 통제 모델

**채택 완료(구현됨)**: Least privilege, default-deny. 표에 없는 역할·action·상태 조합은 항상 거부(kernel 계약, PR #373 구현, 이견 없음).

| 역할·작업 | 허용 상태와 필수 evidence |
| --- | --- |
| `HOLDOUT_AUTHOR / READ·WRITE` | `ACCESS_AUTHORIZED` 또는 `AUTHORING`, unfrozen, current authorization revision |
| `DATASET_CUSTODIAN / READ` | 4개 상태 중 하나, 독립 발급된 Custodian access grant |
| `DATASET_CUSTODIAN / FREEZE` | `REVIEW_READY`, authored 40, 전수 검토, 네 leakage 축 0 evidence |
| `PROTECTED_RUNNER / READ·RUN` | `FROZEN`, Freeze Receipt, execution authorization, #178 Adapter·Index·config binding |

## 6. Credential/Secret 관리

**채택(안)**: 저장소 기존 관행([`backend/app/core/config.py:234-247`](../../../backend/app/core/config.py) HMAC key, [`:249-257`](../../../backend/app/core/config.py) snapshot encryption key) — LOCAL 환경만 placeholder 허용, 그 외 환경은 실값 강제 + fail-closed validator(기동 시점 `ValueError`) — 를 protected credential에도 동일 적용한다. 공통 조건(가빈·현우 이견 없음): credential은 repository/`.env` 미저장, 실행 시점 단기 주입, 종료 후 폐기/회전, `workflow_dispatch` 수동 실행 + required reviewer.

**차단 상태(가빈)**: 이전 초안에서 "authoring=현우 계정의 관리형 환경(회사 지급 기기, SSO)"이라고 쓴 건 **실제로 존재하지 않는 것을 확정처럼 쓴 것**이었다. 사용할 환경의 **소유자, 접근 방식, 로그·artifact 비노출, credential 회전 방법**이 구체적으로 확인될 때까지 **authoring 승인은 차단 상태로 유지**한다.

**확정(가빈)**: 기존 요구사항은 막연한 "2인 승인"이 아니라 **실행 요청자와 분리된 독립 승인자 1인의 승인**이었다 — 2인 승인을 새 요구사항으로 만들려면 별도 합의가 필요하다(이전 초안의 "required reviewer 2인" 제안은 폐기). 우선 **GitHub Environment의 self-review 방지 + 지정된 독립 승인자 1인의 approval evidence를 함께 검증**하는 방향으로 정리한다. (참고로 GitHub required reviewer는 여러 명을 등록해도 그중 한 명만 승인하면 진행되므로, "독립 승인자 1인" 요건을 만족하려면 승인자 목록을 정확히 1인 이상으로 지정하고 self-review 방지가 실제로 작동하는지 별도 검증이 필요하다.)

## 7. 감사·로깅

**채택 완료(구현됨)**: PR #373 — append-only hash-chain journal(synthetic, global monotonic sequence, tamper 검증). 단 정책 검증용이며 실제 durable retention을 증명하지 않음(설계 문서 명시, 이견 없음).

**협의 필요**: 실제 인프라에 아래가 별도로 필요한데, 누가 언제 구현할지(이번 결정 PR 범위인지, 후속 adapter PR 범위인지) 확정 필요.
- DB 수준 UPDATE/DELETE/TRUNCATE 차단
- global sequence + durable head/checkpoint
- backup·restore 검증

**확정(가빈, 신규 정책안)**: authorization·revoke·Freeze·run audit evidence는 **Dataset version 운영 종료 후 최소 1년, legal hold가 있으면 해제 시까지 보존**하는 안에 동의. 단 이는 [`docs/privacy-safety.md:64-69`](../../privacy-safety.md)(90/30/7일 등 다른 값, 전부 미적용 상태)처럼 **기존 저장소 정책의 재사용이 아니라 신규 정책안**이며, 실제 적용은 **Privacy·필요한 외부 승인과 보관 위치가 확정된 뒤에만 가능**하다. 보관 위치와 기술적 삭제·복구 방식은 **Backend·Security 담당자(은영)가 제시**한다.

## 8. 보존·폐기 정책

**확정(가빈)**: Dataset 원본(질문·Gold 본문)도 version 운영 종료 후 **1년을 기본안**으로 하되, **Custodian의 폐기 요청 + Product·Evaluation 책임자(가빈)의 독립 승인**을 거친다.

**확정(가빈) — kernel 계약과의 관계**: 현재 kernel enum(`READ|WRITE|FREEZE|RUN`, `GRANT|REVOKE|EXPIRE`, `DENIED|INTENT|SUCCEEDED|UNKNOWN`)에 폐기 이벤트를 **억지로 끼워 넣지 않는다.** 대신 [`docs/contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md:188-203`](../../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md) 선례처럼 **삭제 전 `INTENT`(Dataset version/digest, 승인, legal hold·backup 조건 결속) → 삭제 후 `SUCCEEDED`/`UNKNOWN` → 재조정 절차**를 갖는 **별도 disposal audit 계약**으로 분리한다. 이는 **공유 계약 변경**이므로 이 문서와 별도로 계약·구현·테스트가 필요하다(이번 PR 범위 밖, 후속 작업으로 이관).

## 9. 백업·복구

**확정(가빈, 기본안)**: mutable authoring 기간에는 **일 단위 backup**, Freeze 및 version 변경 시점에는 **별도 snapshot**, **분기별 restore test**. 구체적인 저장소·암호화·접근 통제와 실행 담당자는 **Backend·Security(은영)가 확정**하고, Custodian은 **비민감 검증 evidence**만 확인한다.

## 10. 정기 접근 검토 주기

**확정(가빈)**: 실행 전 유효성 검증 + **최대 분기별 정기 검토**에 동의. **기술적 검토·revoke 실행은 Backend·Security(은영) 담당, 독립 정책 검토는 Product·Privacy(가빈) 담당**으로 분리한다. 저장소에는 **identity나 보호 위치가 포함되지 않은 검토 receipt만** 남긴다.

## 11. 사고 대응 절차 (Incident Response)

**채택(안) 후보**: 저장소에 전용 IR 문서 선례가 없어(`docs/runbooks/`는 장애 복구용이지 보안사고 대응 프레이밍이 아님) 현우 제안 7단계가 이 저장소의 첫 IR 절차가 된다.
1. authoring과 Runner 실행 중단
2. 관련 grant revoke 및 credential 회전
3. audit와 관련 증빙 보존
4. 영향받은 Freeze Receipt와 실행 승인 무효화
5. 접근·노출 범위 조사
6. 필요한 경우 Dataset 재검토·재작성·재Freeze
7. 독립 Custodian 재승인 후 실행 재개

**확정(가빈)**: 5단계(조사)를 다음과 같이 분리한다.
- **Privacy 영향 판단**: 가빈이 담당
- **Security 기술 조사**: 원칙적으로 Backend·Security 담당자(은영)가 수행하되, **은영이 통제 구현자이거나 사고 당사자인 경우엔 그때 명시적으로 지정한 독립 Security 조사자**가 맡는다. **지혜를 별도 합의 없이 자동 대체 조사자로 고정 지정하지 않는다** — 이전 초안의 자동 전환 제안은 폐기, 매 사고마다 별도 합의로 지정한다.

상세 사고 자료는 **비공개 보관**하고, 저장소에는 **비민감 요약 evidence만** 남긴다(증빙 위치는 `docs/validation/rag/issue-273/` 아래 사고별 report — 내용은 비민감 요약에 한정).

## 12. 예외 처리 절차

**확정(가빈)**: HOLDOUT은 긴급 진료 데이터가 아니므로 **break-glass 예외는 두지 않고 표준 grant/revoke 절차만 허용**하는 안에 동의.

(참고: [`docs/privacy-safety.md:60`](../../privacy-safety.md)의 "예외 처리하지 않습니다" 문장은 복약 가이드·챗봇 Production 배포 차단에 대한 것으로 이 시나리오의 직접 선례는 아니지만, "예외를 만들지 않는다"는 유사 기조로 참고했다.)

## 13. 문서 재검토 주기·만료일

**확정(가빈)**: **infrastructure adapter 연결 PR, 역할·환경·정책 변경 시** 재검토하고, 변경이 없더라도 **연 1회 정기 재검토**를 병행하는 안에 동의.

## 14. 관련 문서·참조

- #368, #273, PR #373, PR #366
- [Issue #368 Security Kernel 설계](../../designs/ceohwj/issue-368-protected-retrieval-runner-security-kernel-design.md)
- `docs/validation/rag/issue-273/protected-runner-foundation.md`
- #368 코멘트, PR #386 리뷰(가빈·현우, 2026-09-09)

## 완료 후 상태 (현재)

policy foundation: `IMPLEMENTED` / effective enforcement: `NOT_IMPLEMENTED` / infrastructure adapter: `NOT_IMPLEMENTED` / HOLDOUT access authorization: `NOT_RECORDED` / HOLDOUT authored: `0` / HOLDOUT Freeze: `NOT_STARTED` — 위 governance 결정은 방향 합의일 뿐이며, **실제 인프라·보관 위치·credential·SQL 통제가 구현·테스트되기 전까지 #368은 Open, `effective_enforcement_status=NOT_IMPLEMENTED`, HOLDOUT 미승인 상태를 유지한다**(가빈 요청).
