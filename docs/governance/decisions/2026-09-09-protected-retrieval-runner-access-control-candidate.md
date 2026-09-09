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

아래 각 절은 **채택(안)**(이미 합의됐거나 저장소 관행을 그대로 따르는 부분 — 반영 방법까지 명시)과 **협의 필요**(구체적으로 무엇을 결정해야 하는지 질문 형태로 명시)를 구분한다. 저장소에 전례가 없는 협의 항목에는 **의료 분야 실무 기본값**을 함께 제안해 빠른 의사결정을 돕는다 — 단 이는 법적 자문이 아니라 업계 통용 관행이며, 최종 적용 전 Privacy·Legal 검토가 별도로 필요하다.

## 1. 목적·배경

HOLDOUT Dataset(질문·Gold·hard negative)을 일반 개발·CI 접근으로부터 격리하고, 승인·역할·감사 경계 없이 실행 표면이 먼저 열리는 것을 막는다. PR #373은 인프라 독립 security kernel과 synthetic adapter만 구현했고, 실제 저장 인프라는 이번 결정 이후 후속 PR에서 연결한다.

## 2. 범위 (Scope / Out-of-scope)

포함: PostgreSQL DB/schema 격리, Owner/Author/Custodian/Runner role과 deny 정책, Credential 주입 환경, Audit 보존 기간, Backup/revoke/incident-response/재검토 주기.

제외: 실제 HOLDOUT 작성·Freeze·실행, `run-protected-holdout` 구현, Retrieval/Metric/Release. 승인 전에는 PostgreSQL migration, secret, protected path를 구현하지 않는다.

## 3. DB/Schema 격리

**협의 필요 — 질문**: 기존 PostgreSQL 인스턴스 안에 **①별도 database**를 둘지 **②protected 전용 schema**를 둘지. 현우는 ①을 먼저 검토하자는 입장, 가빈은 ②(schema)에 동의하되 "분리만으로 완료 처리하지 말 것"을 조건으로 걸었다. HOLDOUT 규모(40문제)를 고려하면 ②가 운영 부담이 적지만, ①이 격리 강도는 더 높다 — **이 트레이드오프에 대한 최종 결정이 필요**하다.

**의료 분야 실무 기본값**: 규제 대상 데이터라 해도 물리적으로 별도 DB 인스턴스를 요구하는 경우는 드물고, 역할 기반 접근 통제(RBAC)+감사 로그가 갖춰진 **논리적 분리(schema/스키마 단위)면 충분**하다고 보는 게 일반적이다(별도 인스턴스는 오히려 백업·모니터링·커넥션풀을 이중으로 운영해야 해서 40문제 규모엔 과잉 대응). **제안: ②schema 방식 채택**, 단 가빈이 요구한 실통제 검증(REVOKE, default privilege, owner 비공유)을 조건으로 건다.

참고: 아래 ①~③에서 보듯 default privilege 거부 정책이 이미 role 단위로 파라미터화되어 있어([`configure-app-role.sql`](../../../infra/docker/postgres/configure-app-role.sql)), schema 방식으로 가더라도 이 스크립트를 protected schema 대상으로 한 번 더 실행하는 정도로 확장 가능 — database 방식 대비 구현 난도 차이가 크지 않다는 근거이기도 하다. [Source Snapshot DB 상태 전이 결정](./2026-09-08-source-snapshot-db-transition.md)도 같은 저장소에서 SECURITY DEFINER 함수 + PUBLIC EXECUTE 회수로 protected DB-owned 연산을 이미 다룬 선례다.

**채택(안) — 이미 구현된 동일 패턴 확장**: 아래 ①~③은 새로 설계할 게 아니라 [`infra/docker/postgres/configure-app-role.sql`](../../../infra/docker/postgres/configure-app-role.sql)이 `migration_user`/`app_user`에 이미 적용 중인 패턴을 protected role에 그대로 확장하는 것이다(현우 지적으로 인용 정정).
- ① 전용 role 분리: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`로 관리 권한 없는 role 생성 ([:24-30](../../../infra/docker/postgres/configure-app-role.sql), [:60-66](../../../infra/docker/postgres/configure-app-role.sql))
- ② 스키마 권한 기본 회수: `REVOKE CREATE ON SCHEMA public FROM app_user` ([:81-85](../../../infra/docker/postgres/configure-app-role.sql))
- ③ default privilege로 동일 거부 정책 유지: `ALTER DEFAULT PRIVILEGES FOR ROLE migration_user ... REVOKE UPDATE ON SEQUENCES FROM app_user` ([:132-138](../../../infra/docker/postgres/configure-app-role.sql)), 승인된 함수에만 `GRANT EXECUTE` ([:97-103](../../../infra/docker/postgres/configure-app-role.sql))

**협의 필요 — 질문(신규 항목)**: ④ **PUBLIC 권한 회수**는 이 스크립트에 없는 진짜 신규 항목이다 — `configure-app-role.sql`은 `app_user`의 `CREATE`만 개별 회수할 뿐, PostgreSQL이 기본으로 모든 role에 부여하는 `PUBLIC`의 schema `USAGE`는 그대로 둔다. Protected schema/DB에서는 `REVOKE ALL ON SCHEMA <protected> FROM PUBLIC`을 명시적으로 추가할지, 그리고 이걸 실제 SQL로 검증할 시점(이번 PR인지 후속 adapter PR인지)을 확정해야 한다.

## 4. 역할·책임 + 직무 분리 (Segregation of Duties)

**채택(안)**: PR #373 kernel이 이미 구현한 3개 역할을 그대로 사용한다.
- `HOLDOUT_AUTHOR`: 질문·Gold 작성 (제안: 정현우)
- `DATASET_CUSTODIAN`: 검토·Freeze (제안: 송은영)
- `PROTECTED_RUNNER`: Freeze된 Dataset 읽기·실행 (승인된 service identity)
- 송은영이 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜로 전환
- Author/Runner grant issuer는 subject 및 control implementation participant와 달라야 함(self-approval 차단, kernel에 이미 구현됨)
- 송은영 본인의 READ/FREEZE grant는 권가빈이 `PRODUCT_SAFETY_REVIEWER` 역할로 독립 발급 (kernel `ProtectedApprovalRole`에 이미 존재)

여기에 더해, 실제 DB 인프라 레벨에서 **"protected owner/migration role"**(DB 객체 소유·migration 전용, `NOLOGIN`)을 신설 — 이건 kernel role과 충돌하지 않는 별도 layer이므로 이견 없이 채택. 이 role 설계는 [Source Snapshot DB 상태 전이 결정](./2026-09-08-source-snapshot-db-transition.md)의 "Runtime은 owner/superuser 또는 owner 역할의 멤버로 운영하지 않는다" 원칙과 정합해야 한다 — 같은 저장소 안에 이미 확정된 DB-owned 연산 경계이므로, protected owner role을 신설할 때 이 결정과 상충하지 않는지 구현 시점에 재확인 필요.

**협의 필요 — 질문**: 현우가 제안한 "approval role"(grant/revoke·Freeze 승인 evidence 기록 전용 governance role)을 kernel의 `ProtectedApprovalRole`과 동일하게 취급할지, 아니면 DB 레벨에서 별도 역할로 다시 만들지 확인 필요.

## 5. 접근 통제 모델

**채택 완료(구현됨)**: Least privilege, default-deny. 표에 없는 역할·action·상태 조합은 항상 거부(kernel 계약, PR #373 구현, 이견 없음).

| 역할·작업 | 허용 상태와 필수 evidence |
| --- | --- |
| `HOLDOUT_AUTHOR / READ·WRITE` | `ACCESS_AUTHORIZED` 또는 `AUTHORING`, unfrozen, current authorization revision |
| `DATASET_CUSTODIAN / READ` | 4개 상태 중 하나, 독립 발급된 Custodian access grant |
| `DATASET_CUSTODIAN / FREEZE` | `REVIEW_READY`, authored 40, 전수 검토, 네 leakage 축 0 evidence |
| `PROTECTED_RUNNER / READ·RUN` | `FROZEN`, Freeze Receipt, execution authorization, #178 Adapter·Index·config binding |

## 6. Credential/Secret 관리

**채택(안)**: 저장소 기존 관행([`backend/app/core/config.py:234-247`](../../../backend/app/core/config.py) HMAC key, [`:249-257`](../../../backend/app/core/config.py) snapshot encryption key) — LOCAL 환경만 placeholder 허용, 그 외 환경은 실값 강제 + fail-closed validator(기동 시점 `ValueError`) — 를 protected credential에도 동일 적용한다(현우 지적으로 인용 정정: 이전에 인용했던 `:149-158`은 Fernet 형식 검증만 하는 별개 validator였다). 공통 조건(가빈·현우 이견 없음): credential은 repository/`.env` 미저장, 실행 시점 단기 주입, 종료 후 폐기/회전, `workflow_dispatch` 수동 실행 + required reviewer.

**협의 필요 — 질문**: 작성·검토(authoring)는 CI 밖 별도 환경에서 한다는 방향엔 이견이 없는데, **그 "별도 환경"이 실제로 무엇인지가 미정**이다 — 사내 특정 인원의 로컬 환경인지, 별도 VM/컨테이너인지, 혹은 GitHub Codespace 같은 관리형 환경인지 확정 필요. 또한 Freeze 이후 평가용 GitHub Actions protected Environment는 **저장소에 선례가 없어**(`.github/workflows/checks.yml`에 `environment:` 설정 없음 확인) required reviewer 명단과 전용 runner 구성을 새로 설계해야 한다.

**의료 분야 실무 기본값**: 민감 테스트 데이터 authoring은 개인 노트북이 아니라 **회사가 관리하는 계정 기반 환경(SSO 로그인 기록이 남는 관리형 워크스테이션 또는 사내 VDI)**에서 하는 게 일반적 — 접근 자체가 누구 계정으로 언제 이뤄졌는지 감사 가능해야 하기 때문. Freeze 이후 실행은 **GitHub Actions protected Environment + 최소 2인 이상 required reviewer**(승인자 1인 단독 결정 방지)가 업계에서 흔히 쓰는 기준이다. **제안: authoring=현우 계정의 관리형 환경(회사 지급 기기, SSO), 실행=protected Environment + required reviewer 2인(가빈+지혜 또는 가빈+독립 Custodian).**

## 7. 감사·로깅

**채택 완료(구현됨)**: PR #373 — append-only hash-chain journal(synthetic, global monotonic sequence, tamper 검증). 단 정책 검증용이며 실제 durable retention을 증명하지 않음(설계 문서 명시, 이견 없음).

**협의 필요 — 질문 1**: 실제 인프라에 아래가 별도로 필요한데, 누가 언제 구현할지(이번 결정 PR 범위인지, 후속 adapter PR 범위인지) 확정 필요.
- DB 수준 UPDATE/DELETE/TRUNCATE 차단
- global sequence + durable head/checkpoint
- backup·restore 검증

**협의 필요 — 질문 2 (보존 기간)**: 저장소 관행([`docs/privacy-safety.md:64-69`](../../privacy-safety.md) 표 + "Privacy 승인 전까지 미적용" 단서)을 그대로 따를지 확인. 현우 초안(authorization·revoke·Freeze·run audit evidence는 운영 종료 후 최소 1년, legal hold 우선, 일반 실행 로그와 보존기간 분리)에 **가빈·현우 모두 동의하는지 명시적 확인 필요** — 아직 "초안"일 뿐 확정 답변은 없었다.

**의료 분야 실무 기본값**: 보안 감사 로그는 **최소 1년 보존이 업계에서 널리 쓰이는 하한선**(PCI DSS 등 보안 표준의 로그 보존 요구사항과 동일 궤)이고, 의료 데이터를 다루는 조직은 사고가 뒤늦게 드러나는 경우가 많아 **접근 관련 감사 기록은 이보다 길게(3년 전후)** 잡는 경우도 흔하다. HOLDOUT은 환자 원본이 아니라 평가용 합성 데이터이므로 **현우 제안(최소 1년)을 하한으로 채택**하고, legal hold 발생 시 자동 연장하는 조건만 명확히 하면 충분해 보인다.

## 8. 보존·폐기 정책

**협의 필요 — 질문**: 7번은 audit evidence(감사 기록) 보존기간이고, 이건 Dataset **원본**(질문·Gold 본문) 자체의 폐기 정책이다. 서로 다른 대상이므로 별도 확정 필요.
- Dataset 원본은 언제 폐기하는가 — 운영 종료 시점? 아니면 별도 주기?
- 폐기 승인자는 누구인가 — Custodian 단독인가, 독립 승인자도 필요한가?
- 폐기했다는 증빙은 어떻게 남기는가 — audit journal에 `DENIED`/`REVOKE`류 항목으로 남기는가, 별도 기록이 필요한가?

**의료 분야 실무 기본값**: 민감 데이터 폐기는 **①문서화된 보존기간 만료 후 ②Data Owner/Custodian 승인 ③검증 가능한 삭제(단순 삭제가 아니라 삭제 완료를 확인하고 기록)** 3단계를 갖추는 게 표준이다. 단독 관리자가 임의로 지우는 방식은 지양한다. **제안: 폐기 시점=7번 audit 보존기간과 동일(운영 종료 후 최소 1년 뒤), 승인자=Custodian, 증빙=audit journal에 폐기 완료 이벤트 append.**

## 9. 백업·복구

**협의 필요 — 질문**: 가빈 코멘트에서 "revoke, incident response, backup 복구 검증"으로 한데 묶여 언급됐을 뿐, 구체 내용이 없다.
- Backup 주기는? (예: 일 단위 스냅샷)
- 복구 테스트는 언제·누가 실행하고 검증하는가?
- 이 항목은 8번(폐기)과 저장 대상이 겹치므로, 같은 PR/문서에서 함께 정리할지 확인 필요.

**의료 분야 실무 기본값**: **일 단위 백업 + 분기별 복구 테스트(restore drill)**가 의료 데이터를 다루는 조직의 흔한 최소 기준이다(백업만 있고 복구가 실제로 되는지 검증 안 하는 경우가 사고로 이어지는 대표 사례라, 정기 복구 테스트를 같이 요구하는 게 관행). **제안: 일 단위 backup, 분기별 복구 테스트 — 10번(정기 접근 검토)과 같은 분기 주기에 맞춰 같은 담당자가 함께 점검.**

## 10. 정기 접근 검토 주기

**채택(안) 후보**: 저장소에 고정 재검토 주기 선례가 없어(`configure-app-role.sql`은 최소 권한 프로비저닝만 정의) 현우 제안이 사실상 이 저장소의 첫 기준이 된다 — authoring·Freeze·run 실행 전 매번 유효성 재검증 + 정기 검토 최대 분기별, 인원/역할 변경 시 즉시 revoke.

**협의 필요 — 질문**: "정기 검토"의 **실행 담당자**가 아직 없다 — 분기별 검토를 누가(Custodian? 별도 감사자?) 수행하고, 검토 결과를 어디에 기록하는지 확정 필요.

**의료 분야 실무 기본값**: 접근 권한을 **부여한 사람이 스스로 재검토하지 않고, 독립된 역할이 검토**하는 게 원칙(4번의 직무 분리와 같은 논리). **제안: 검토 담당자=권가빈(또는 그 시점의 독립 Custodian), 검토 결과는 이 문서와 같은 `docs/governance/decisions/` 아래 분기별 기록으로 남김.**

## 11. 사고 대응 절차 (Incident Response)

**채택(안) 후보**: 저장소에 전용 IR 문서 선례가 없어(`docs/runbooks/`는 장애 복구용이지 보안사고 대응 프레이밍이 아님) 현우 제안 7단계가 이 저장소의 첫 IR 절차가 된다.
1. authoring과 Runner 실행 중단
2. 관련 grant revoke 및 credential 회전
3. audit와 관련 증빙 보존
4. 영향받은 Freeze Receipt와 실행 승인 무효화
5. 접근·노출 범위 조사
6. 필요한 경우 Dataset 재검토·재작성·재Freeze
7. 독립 Custodian 재승인 후 실행 재개

**협의 필요 — 질문**: 가빈이 요청한 "담당자·증빙 방식"이 아직 없다 — 각 단계를 누가 실행하는지(예: 1·2번은 Custodian, 5번은 Security 검토자), 증빙을 어디에 남기는지(`docs/validation/` 아래 report 형식인지) 확정 필요.

**의료 분야 실무 기본값**: 사고 대응은 **"사고 지휘자(incident commander)" 역할을 미리 한 명 지정**해두고, 지휘자가 단계별 담당자를 조율하는 방식이 표준이다(사고 중 역할이 불명확하면 대응이 지연되는 게 흔한 실패 사례). **제안: 지휘자=Custodian(현 시점 은영 또는 독립 Custodian), 1·2·4·6·7단계=Custodian, 5단계(조사)=Security 검토자(가빈) 겸임, 증빙은 `docs/validation/rag/issue-273/` 아래 사고별 report 파일로 남김.**

## 12. 예외 처리 절차

**협의 필요 — 질문**: [`docs/privacy-safety.md:60`](../../privacy-safety.md) "승인표나 수동 검토만으로 이 차단을 예외 처리하지 않습니다"는 인용 자체는 정확하지만, 이 문장은 **복약 가이드·챗봇의 Production 배포 차단**에 대한 것이지 protected dataset 접근 예외에 대한 것이 아니다 — 즉 이건 이 시나리오의 **직접 선례가 아니라 "예외를 만들지 않는다"는 유사 기조에서의 유추**다(현우 지적 반영). 이 유추를 그대로 적용해 **긴급 접근을 포함해 예외 승인 경로를 별도로 두지 않고, 표준 grant/revoke 절차만 인정**할지 가빈·현우 모두 동의하는지 확인 필요 — 아직 아무도 답하지 않은 새 질문이다.

**의료 분야 실무 기본값 (주의: 저장소 기존 기조와 다를 수 있음)**: 실제 의료 데이터 시스템(EHR 등)에서는 "예외를 아예 안 만든다"보다 **"break-glass"(비상 접근) 절차 — 평소보다 강화된 감사와 함께 즉시 접근을 허용하고, 24~48시간 내 의무적 사후 검토(post-hoc review)로 정당성을 소명**하는 방식이 더 흔하다. 다만 이건 실시간 진료처럼 "지금 당장 막으면 위험한" 상황을 전제로 한 관행이고, **HOLDOUT은 평가용 합성 데이터라 그 정도 긴급성이 없다** — 그래서 저장소 기존 기조(예외 없음)를 따르는 게 이 케이스엔 더 맞아 보인다. **제안: break-glass 없이 저장소 기조대로 예외 미허용 유지, 단 이 판단 근거(긴급성 낮음)를 문서에 남긴다.**

## 13. 문서 재검토 주기·만료일

**채택(안) 후보**: 저장소 관행(`docs/contracts/targets/post-mvp-1/*.md`의 `Last verified` 필드)은 고정 주기가 아니라 관련 PR·코멘트 발생 시점마다 갱신하는 방식이다. 같은 관행을 따라, 실제 infrastructure adapter 연결 PR이 열릴 때 이 문서를 재검토·갱신하는 것을 트리거로 제안.

**협의 필요 — 질문**: 이 방식(이벤트 트리거만, 고정 주기 없음)에 동의하는지, 아니면 별도로 최소 재검토 주기(예: adapter 연결이 오래 지연될 경우 대비 6개월)를 추가로 둘지 확인 필요.

**의료 분야 실무 기본값**: 보안·개인정보 관련 정책 문서는 이벤트 트리거와 별개로 **최소 연 1회 정기 재검토**를 병행하는 게 일반적이다(트리거가 오래 안 오면 정책이 무기한 방치되는 걸 막기 위함). **제안: infra adapter 연결 PR 시점 트리거 + 그와 별개로 연 1회(예: 매년 9월) 정기 재검토를 병행.**

## 14. 관련 문서·참조

- #368, #273, PR #373, PR #366
- [Issue #368 Security Kernel 설계](../../designs/ceohwj/issue-368-protected-retrieval-runner-security-kernel-design.md)
- `docs/validation/rag/issue-273/protected-runner-foundation.md`
- #368 코멘트: [가빈](https://github.com/AI-HealthCare-05/AH_05_04/issues/368), 현우 답변(2026-09-09)

## 완료 후 상태 (현재)

policy foundation: `IMPLEMENTED` / effective enforcement: `NOT_IMPLEMENTED` / infrastructure adapter: `NOT_IMPLEMENTED` / HOLDOUT access authorization: `NOT_RECORDED` / HOLDOUT authored: `0` / HOLDOUT Freeze: `NOT_STARTED` — 이 결정이 실제로 확정·구현되기 전까지 #368은 Open, 위 상태를 유지한다.
