# Protected HOLDOUT Dataset 폐기 감사 계약 (#425)

- 문서 상태: **Proposed**. 승인된 Target도, 현재 실행 계약도 아니다.
- 계약 식별자 제안: `protected-holdout-disposal-audit-v1` (승인 전 제안 식별자)
- 문서 작성·구현: 정현우 (`@ceohwj`) — AI/RAG
- 정책 결정·담당 리뷰: 권가빈 (`@hazelnutflavoured`) — Product·Privacy·Safety·Evaluation
- Dataset Custodian·접근 통제 검토: 송은영 (`@phina-io`) — Backend·Security
- 상위 결정: [`PD-368-20260909`](../../../governance/decisions/2026-09-09-protected-retrieval-runner-access-control.md) §8 (상태: **Approved Target · Not implemented**, PR #432에서 승격)
- 추적: [#425](https://github.com/AI-HealthCare-05/AH_05_04/issues/425) · 상위 [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) · [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273)

## 0. 이 문서가 하지 않는 것

이 문서는 **계약 정의**다. 폐기를 실행하지 않고, 실행을 승인하지도 않는다.

- 실제 HOLDOUT Dataset 원본 폐기는 **차단 상태를 유지한다.** 해제 조건은 §6에 있다.
- 코드·migration·schema를 이 문서와 함께 변경하지 않는다. §4·§5의 구조는 승인 후 별도 PR에서 구현한다.
- `#368` infrastructure adapter(PR #432)가 증빙에 고정한 `disposal_status = "BLOCKED_BY_ISSUE_425"`를 이 문서가 바꾸지 않는다. 그 값의 전환은 §6 조건을 모두 충족한 구현 PR에서만 이뤄진다.
- 상위 `PD-368`은 `Approved Target · Not implemented`다. 승인된 **목표**이지 현재 실행 계약이 아니며, 이 문서의 채택이 폐기 실행 승인을 의미하지 않는다.
- **`PD-368` §8이 열어 둔 "폐기 후 재현 요구" 정책 결정은 이 문서가 내리지 않는다.** "재현은 provenance·integrity 확인에 한정된다"를 확정된 사실로 서술하지 않는다(§7-D1).

## 1. 배경 — `PD-368` §8이 분리를 지시한 이유

`PD-368` §8은 폐기 이벤트를 kernel의 기존 enum(`READ|WRITE|FREEZE|RUN`, `GRANT|REVOKE|EXPIRE`, `DENIED|INTENT|SUCCEEDED|UNKNOWN`)에 끼워 넣지 않기로 했다. 폐기는 Dataset에 대한 **접근**이 아니라 Dataset 자체의 **소멸**이고, 같은 축에 넣으면 "접근이 승인되었다"와 "원본이 사라졌다"가 한 enum에서 구분되지 않는다.

동시에 §8은 폐기를 **삭제 전 `INTENT` → 삭제 후 `SUCCEEDED`/`UNKNOWN` → 재조정**의 형태로 규정했다. 이 형태는 kernel이 이미 쓰는 형태와 같다. 따라서 이 계약은 **형태는 재사용하고 축은 분리한다.**

## 2. 현재 구현 조사

`origin/develop` `79934df3` 기준이다. 라인 번호 대신 심볼 이름으로 인용한다.

| 확인한 사실 | 근거 | 이 계약에 미치는 영향 |
| --- | --- | --- |
| 폐기 **실행** 구현은 없고, **차단 표식만** 있다 | `protected_retrieval_infrastructure_evidence.py`의 고정 기대값 `"disposal_status": "BLOCKED_BY_ISSUE_425"` (다른 값이면 `RuntimeError`). 그 외 disposal 구현은 검색 결과 0건 | 기존 동작을 바꾸지 않는 신규 계약이다. **이 문서는 그 표식을 해제하지 않는다**(§6) |
| audit journal은 **단일 hash chain**이다 | `protected_retrieval.py` · `AuthorizationAuditEntry` / `OperationAuditEntry`의 `sequence`·`previous_entry_sha256`·`entry_sha256`, `audit_entry_sha256` | §4에서 별도 journal을 두지 않는 근거다 |
| variant 구분은 `event_kind` 판별자다 | `ProtectedAuditEventKind` = `AUTHORIZATION`\|`OPERATION`, `ProtectedAuditEntry = AuthorizationAuditEntry \| OperationAuditEntry` | 세 번째 variant 추가가 기존 구조와 같은 방식이다 |
| `INTENT`/`UNKNOWN` 미종결은 후속 실행을 **차단**한다 | `protected_retrieval.py` · `_resolve_operation_history`·`_resolve_guarded_operation_history`가 `RECONCILIATION_REQUIRED`로 fail-closed | §4-4의 재조정 절차를 새로 발명하지 않고 같은 규칙을 적용한다 |
| 자기 승인은 이미 금지된다 | `ProtectedAuditReason.SELF_APPROVAL_DENIED` | §4-2의 승인 결속이 기존 통제와 일관된다 |
| Dataset **운영 종료 상태가 이미 있다** | `evaluation/schemas/authoring.py` · `DatasetStatus` = `DRAFT`\|`FROZEN`\|`RETIRED`, schema `1.3.0`의 `DatasetStatus` | §3에서 **새 상태를 만들지 않는 근거**다 |
| 그러나 `RETIRED`에는 증빙 요건이 없다 | `DatasetManifest.validate_manifest`는 `FROZEN`에만 `frozen_at`·승인된 provenance를 요구하고 `RETIRED`에는 아무 조건이 없다 | §3의 유일한 실질 변경 지점이다 |
| kernel의 Dataset 상태축은 **접근 통제용**으로 별개다 | `ProtectedDatasetState` = `ACCESS_AUTHORIZED`\|`AUTHORING`\|`REVIEW_READY`\|`FROZEN` | 두 축을 합치지 않는다(§3 대안 표) |
| Dataset 동일성은 3요소로 고정된다 | manifest schema `1.3.0`의 `dataset_code`·`dataset_version`·`manifest_sha256` | §4-1 폐기 대상 결속 키다 |

## 3. Dataset version의 운영 종료·폐기 가능 상태

`PD-368` §8은 "Dataset version의 명시적인 운영 종료·폐기 가능 상태"를 폐기의 선행 조건으로 요구한다. 조사 결과 **두 요구는 성격이 다르며, 앞의 하나는 이미 존재한다.**

| 요구 | 현재 | 이 계약의 제안 |
| --- | --- | --- |
| 운영 종료 | `DatasetStatus.RETIRED`가 이미 있다 | **새 상태를 만들지 않는다.** `RETIRED`를 그대로 쓴다 |
| 폐기 가능 | 없다 | **새 상태를 만들지 않는다.** 폐기 가능 여부는 상태가 아니라 §4-2의 승인 결속으로 판정한다 |

새 enum 값을 도입하지 않는 이유는 `CONTRIBUTING.md`의 「새로운 status·enum을 추가하기 전에 기존 모델로 표현할 수 없는 이유를 확인한다」이다. "폐기 가능"을 상태로 만들면 승인과 상태가 이중 정본이 되고, 상태만 바꿔 승인을 우회하는 경로가 생긴다. 승인 결속 하나만 정본으로 둔다.

### 3-1. `RETIRED`에 필요한 보완 (schema 변경)

`RETIRED`가 폐기의 선행 조건이 되려면, 지금처럼 아무 증빙 없이 전이 가능해서는 안 된다. `FROZEN`이 `frozen_at`과 승인된 provenance를 요구하는 것과 같은 수준을 요구한다.

- `retired_at`: `RETIRED`일 때 필수, 그 외 상태에서는 `null`이어야 한다 (`frozen_at`과 동일한 규칙).
- `RETIRED` 전이는 `FROZEN`을 거친 manifest에만 허용한다. `DRAFT → RETIRED`는 승인된 적 없는 Dataset을 폐기 경로에 올리는 것이므로 막는다.
- `review_provenance.team_gold_status`가 `APPROVED`이고 승인자가 `DATASET_CUSTODIAN`이어야 한다(`FROZEN`과 동일).

이는 `rag-eval.dataset-manifest` schema 변경이므로 **`1.4.0` 신규 버전**이 필요하다. 기존 `1.3.0` 산출물은 재작성하지 않는다.

## 4. 폐기 감사 계약

### 4-1. 기존 journal에 세 번째 variant를 추가한다

`ProtectedAuditEventKind`에 `DISPOSAL`을, `ProtectedAuditEntry` union에 `DisposalAuditEntry`를 추가한다. **별도 journal을 만들지 않는다.**

근거: journal의 위변조 탐지는 `sequence`와 `previous_entry_sha256`로 이어진 **하나의 chain**에 의존한다. 폐기 기록만 다른 journal에 두면, 폐기 기록 전체가 사라져도 남은 chain은 여전히 정합해 탐지되지 않는다. 같은 chain에 넣으면 폐기 기록의 삭제가 `AUDIT_TAIL_TRUNCATED`·`AUDIT_HASH_MISMATCH`로 드러난다. 「축을 분리한다」는 `PD-368` §8의 요구는 **enum 의미의 분리**이지 저장 경계의 분리가 아니다.

### 4-2. `DisposalAuditEntry` 필드

| 필드 | 의미 |
| --- | --- |
| `event_kind` | `DISPOSAL` 고정 |
| `sequence`, `event_id`, `recorded_at`, `previous_entry_sha256`, `entry_sha256` | 기존 variant와 동일한 chain 필드 |
| `disposal_request_id` | 하나의 폐기 요청을 `INTENT`와 종결 기록이 공유하는 키 |
| `dataset_id`, `dataset_version`, `manifest_sha256` | 폐기 대상 Dataset version의 identity |
| `protected_artifact_sha256` | 폐기 대상 원본 digest. 폐기 후 이 값만 남고 원본은 남지 않는다 |
| `requested_by` | Custodian (`ProtectedPrincipal`) |
| `approved_by` | 독립 승인자 (`ProtectedApprovalPrincipal`). `requested_by`와 같으면 `SELF_APPROVAL_DENIED` |
| `approval_source_event_id`, `approval_source_raw_sha256` | 승인 증빙 결속. 기존 `AuthorizationAuditEntry`와 동일한 방식 |
| `legal_hold_state` | `ABSENT`\|`PRESENT`\|`UNVERIFIED`. `ABSENT`가 아니면 폐기하지 않는다 |
| `backup_disposition` | `NO_BACKUP_EXISTS`\|`BACKUP_DISPOSED`\|`BACKUP_RETAINED`\|`UNVERIFIED`. 백업이 남아 있으면 "폐기됨"이 아니다 |
| `outcome` | `DatasetDisposalOutcome` (§4-3) |
| `reason_code` | `ProtectedAuditReason` 재사용 |
| `closes_intent` | 선행 `INTENT`를 종결하는 기록인지 |

`legal_hold_state`와 `backup_disposition`에 `UNVERIFIED`를 둔 이유는 fail-closed다. "확인하지 못했다"를 "없다"로 기록할 수 없어야 한다.

### 4-3. `DatasetDisposalOutcome`

`OperationAuditOutcome`을 재사용하지 않고 별도 enum을 둔다(`PD-368` §8).

| 값 | 의미 |
| --- | --- |
| `INTENT` | 삭제 **전** 기록. 대상·승인·legal hold·backup 조건이 이 시점에 결속된다 |
| `SUCCEEDED` | 삭제 성공 확인. `closes_intent = true` |
| `UNKNOWN` | 삭제 시도 후 결과를 확인하지 못함. `closes_intent = false`, 재조정 필요 |

`DENIED`는 포함하지 않는 것을 제안한다 — 승인되지 않은 폐기는 `INTENT`에 도달하지 못하므로 남길 폐기 사실이 없고, 거부는 이미 기존 `OperationAuditOutcome.DENIED`로 기록된다. **다만 이 판단은 검토가 필요하다(§7-D2).**

### 4-4. 순서와 재조정

1. `INTENT` 기록이 **성공적으로 journal에 들어간 뒤에만** 실제 삭제를 시작한다. 순서를 뒤집으면 원본이 사라졌는데 그 사실을 남긴 기록이 없는 상태가 가능해진다.
2. 삭제 후 `SUCCEEDED` 또는 `UNKNOWN`을 기록한다.
3. 같은 `dataset_version`에 종결되지 않은 `INTENT` 또는 `UNKNOWN`이 있으면 **그 Dataset version에 대한 모든 후속 폐기·실행을 차단한다.** kernel이 이미 `_resolve_operation_history`에서 쓰는 `RECONCILIATION_REQUIRED`와 같은 규칙이다.
4. 재조정은 실제 저장 상태를 확인한 사람이 결과를 확정해 종결 기록을 남기는 절차이며, 요청자 단독으로 할 수 없다(§4-2의 독립 승인과 동일).

### 4-5. 감사 기록에 담지 않는 것

Dataset 원문, 질문·Gold 본문, 경로, 접근 주체의 보호 위치는 기록하지 않는다. 남는 것은 digest·정책 식별자·역할·시각·안전한 reason code뿐이다. `ProtectedSecurityError`가 고정 reason code만 노출하는 기존 원칙을 따른다.

## 5. 저장 경계

기존 journal과 같은 저장 경계를 쓴다. 별도 테이블을 제안하지 않는다. 구현 PR에서 필요한 것은 variant 추가에 따른 컬럼·제약이며, 실제 schema는 접근 통제 구현(송은영)과 같은 흐름에서 확정한다. 이 문서는 저장 기술을 확정하지 않는다.

`CONTRIBUTING.md`에 따라 Trigger·RLS·업무 규칙용 DB 함수는 도입하지 않는다. 순서 강제(§4-4)와 승인 검증(§4-2)은 Python 경계에서 명시적으로 수행한다.

## 6. 폐기 차단 해제 조건

아래가 **모두** 충족되기 전에는 실제 HOLDOUT 원본 폐기를 실행하지 않는다.

1. §7-D1(재현 범위) 정책 결정이 내려지고 이 문서에 명문화된다.
2. 이 문서가 승인되고, §3-1 schema 보완과 §4 variant가 구현·테스트된다.
3. `protected_retrieval_infrastructure_evidence.py`의 `disposal_status`가 같은 PR에서 함께 전환된다 — 그 고정 기대값은 폐기가 차단되어 있다는 사실의 기계 기록이므로, 계약만 승인되고 이 값이 남아 있으면 문서와 증빙이 어긋난다.
4. `PD-368` §9의 백업 경계가 확정된다 — 백업에 남은 사본을 확인할 수 없으면 `backup_disposition`을 `UNVERIFIED`로만 기록할 수 있고, 그 상태의 폐기는 "폐기됨"을 주장하지 못한다.

## 7. 결정이 필요한 항목

이 문서가 단독으로 정하지 않는다. 추정해서 채우지 않고 열어 둔다.

### D1. 폐기와 "과거 평가 재현" 요구의 경계 — **권가빈 (Product·Evaluation)**

`PD-368` §8은 이 항목을 "이 문서의 합의 사항이 아니라 별도의 Product·Evaluation 정책 결정이 필요한 사항"으로 명시적으로 열어 두었다. 원본을 폐기하면 Freeze Receipt와 manifest hash 대조로 **identity·무결성은 확인할 수 있으나 재실행 기반 재현은 불가능하다.**

선택지는 두 가지다.

| 안 | 내용 | 결과 |
| --- | --- | --- |
| A | 재현 요구를 provenance·integrity 확인으로 한정 | 폐기 가능. 과거 평가 결과의 재계산은 영구히 포기 |
| B | 원본 재실행 기반 재현을 요구 | 재현 요구가 유지되는 동안 원본 폐기 불가 |

결정 전까지 §6에 따라 폐기는 차단이다.

### D2. `DatasetDisposalOutcome`에 `DENIED`를 두는지 — **권가빈·송은영**

§4-3의 제안은 두지 않는 것이다. "폐기가 거부된 사실"을 폐기 축에 남겨야 한다는 판단이면 추가한다.

### D3. 폐기 감사 기록의 보존 기간 — **권가빈**

`PD-368` §8은 보존 기간의 숫자를 정하지 않았다. 선례인 [`source-artifact-retention-cleanup.md`](./source-artifact-retention-cleanup.md)도 "관련 provenance가 유지되는 동안"까지만 정하고 숫자를 확정하지 않았다. 같은 기준을 쓸지 별도 기간을 둘지 결정이 필요하다.

## 8. 검토한 대안

| 대안 | 판단 |
| --- | --- |
| **별도 disposal journal 신설** | 기각. chain이 분리되어 폐기 기록 전체 삭제가 탐지되지 않는다(§4-1). `PD-368` §8의 분리 요구는 enum 의미의 분리로 충족된다 |
| **기존 `OperationAuditOutcome`에 폐기 값 추가** | 기각. `PD-368` §8이 명시적으로 금지했고, 접근 판정과 소멸 사실이 한 축에서 구분되지 않는다 |
| **`ProtectedDatasetState`에 `RETIRED`·`DISPOSED` 추가** | 기각. 그 축은 접근 통제용이고, 운영 종료는 이미 manifest의 `DatasetStatus.RETIRED`에 있다. 두 축에 같은 개념을 두면 어느 쪽이 정본인지 불명확해진다 |
| **"폐기 가능" 상태를 새로 도입** | 기각. 승인과 상태가 이중 정본이 되어 상태만 바꿔 승인을 우회하는 경로가 생긴다(§3) |
| **삭제 후 한 건만 기록(사전 `INTENT` 없음)** | 기각. 삭제 도중 중단되면 원본이 사라졌는데 그 사실의 기록이 없다. `PD-368` §8과 `source-artifact-retention-cleanup.md` 선례 모두 사전 기록을 요구한다 |
| **`legal_hold`·`backup`을 boolean으로** | 기각. "확인하지 못함"을 "없음"으로 기록하게 되어 fail-closed가 깨진다(§4-2) |

## 9. 담당과 검토 범위

| 역할 | 담당 | 범위 |
| --- | --- | --- |
| 계약 작성·후속 구현 | 정현우 (`@ceohwj`) | kernel variant, schema 보완, 테스트 |
| 정책 결정·담당 리뷰 | 권가빈 (`@hazelnutflavoured`) | D1·D2·D3, 승인 구조, 보존 정책 정합 |
| 접근 통제·저장 경계 검토 | 송은영 (`@phina-io`) | §5 저장 경계, D2 |
| 독립 Custodian 승인 | `PD-368` §3의 지정에 따름 | 실제 폐기 요청·승인 (이 문서 범위 밖) |

외부 승인·공개 게이트는 해당 없다. 계약 정의 단계이며 실제 폐기 실행 전이다.

## 10. 관련 문서

- [`PD-368-20260909`](../../../governance/decisions/2026-09-09-protected-retrieval-runner-access-control.md) §8 — 상위 결정, `Approved Target · Not implemented`
- [Issue #368 Security Kernel 설계](../../../designs/ceohwj/issue-368-protected-retrieval-runner-security-kernel-design.md)
- [`source-artifact-retention-cleanup.md`](./source-artifact-retention-cleanup.md) — 삭제 전 `INTENT` 기록 선례
- `ai_worker/tasks/evaluation/protected_retrieval.py` — journal·chain·재조정 규칙의 현재 구현
- `ai_worker/tasks/evaluation/schemas/authoring.py` · `DatasetStatus` — 운영 종료 상태의 현재 정본
