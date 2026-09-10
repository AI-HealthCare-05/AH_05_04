# Product Decision: RAG Preflight 복합 STALE 우선순위 및 단일 오류 사영 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-173-20260909` |
| 상태 | Proposed · Review pending — 지정 책임 리뷰어 3인 중 2인 승인, 승인 조건 미충족 ([상세](#승인-및-적용-조건)) |
| 제안일 | 2026-09-09 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용 `APPROVED`, 송은영 (`@phina-io`) — Backend·공개 DTO `APPROVED`, 남한솔 (`@solia142`) — 환자 표시·오류 UX **미승인** |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) (`MERGED` `2026-09-09T16:50:53Z`) |
| 승인 현황 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json) |
| 상위 계약 | [`docs/contracts/targets/post-mvp-1/safety-result-v2.md`](../../contracts/targets/post-mvp-1/safety-result-v2.md) |
| 제안 계약 | [`docs/contracts/proposed/post-mvp-1/safety-result-compound-stale-priority-v1.md`](../../contracts/proposed/post-mvp-1/safety-result-compound-stale-priority-v1.md) |

## 목적

RAG Preflight 판정 및 후속 Safety Result 생성 시, 둘 이상의 STALE 원인(처방전 버전 불일치, 공식 의약품 식별 불일치, 런타임 릴리즈 번들 불일치)이 동시에 발생했을 때의 결정적 단일 공개 `fallback_code` 및 내부 `stale_reason` 사영 우선순위를 공식화한다.

## 배경

승인된 목표 계약인 `safety-result-v2.md`는 단일 원인별 오류 매핑(처방 버전 불일치 → `PRESCRIPTION_STALE`, 식별/번들 불일치 → `EXECUTION_CONTEXT_STALE`)을 정의하고 있으나, 복합 STALE 상황에서 환자 공개 DTO로 투영할 단일 fallback_code의 우선순위 규칙을 명시하지 않았다. 저장소 AGENTS 규칙에 따라 공개 error code/meaning의 확정에는 별도의 Decision 및 승인 절차가 필요하므로, 본 결정을 통해 복합 STALE 사영 기준을 제안하고 검토를 요청한다.

## 결정 제안

1. **복합 STALE 단일 사영 우선순위 고정**:
   복수의 STALE 신호가 동시에 발생한 경우, 다음 우선순위에 따라 단일 공개 `fallback_code` 및 내부 `stale_reason`으로 사영한다.
   ```text
   PRESCRIPTION_STALE > IDENTIFICATION_STALE > RUNTIME_RELEASE_STALE
   ```

2. **단일 신호별 사영 규칙**:
   - `PRESCRIPTION_STALE` (최우선): 처방전 버전 불일치는 사용자의 임상 원천 데이터 자체가 변경된 사건이므로, 시스템 내부적 컨텍스트 불일치보다 환자 공개 오류로 항상 우선한다 (`fallback_code="PRESCRIPTION_STALE"`, 내부 `stale_reason=None`).
   - `IDENTIFICATION_STALE`: 처방 버전 불일치가 없을 때, 약제 단위의 공식 의약품 식별 불일치는 런타임 번들 불일치보다 상위 도메인 사유로 우선한다 (`fallback_code="EXECUTION_CONTEXT_STALE"`, 내부 `stale_reason="IDENTIFICATION_STALE"`).
   - `RUNTIME_RELEASE_STALE`: 활성 런타임 릴리즈 번들만 변경된 경우 (`fallback_code="EXECUTION_CONTEXT_STALE"`, 내부 `stale_reason="RUNTIME_RELEASE_STALE"`).

3. **공개 DTO와 내부 상세 분리**:
   모든 Context 불일치 종결은 `AI_JOB=STALE + release_decision=STALE + is_current=false`를 유지하며, 내부 상세 사유(`stale_reason`)는 공개 DTO에 노출하지 않는다.

## 승인 및 적용 조건

본 문서는 `Proposed` 상태이며 그 자체로 확정된 Current 계약이나 Production 활성화를 의미하지 않는다. PR #382의 최신 HEAD에서 지정 책임 리뷰어(권가빈, 송은영, 남한솔)의 승인을 받고 자동화된 계약/단위 테스트가 통과해야 본 Decision과 제안 계약이 승인된 Target으로 편입될 수 있다.

**현재 충족 상태: 미충족 — 소급 충족 불가.** 이 조건은 **병합 전 게이트**다. 지정 책임 리뷰어 3인 중 2인이 승인하고 자동화 검증은 통과했으나 남한솔 (`@solia142`)의 승인 없이 PR #382가 `2026-09-09T16:50:53Z`에 병합되었다. 게이트를 통과하지 못한 채 병합되었다는 사실은 사후에 되돌릴 수 없으며, 지금 시점에 어떤 승인을 추가하더라도 "병합 전에 3인 승인을 받았다"는 근거가 되지 않는다.

아래에 수집한 evidence와 미충족 항목을 기록한다. 이 절의 evidence 기록은 **상태 전이가 아니라 조건 충족 현황의 추적**이다.

### 수집된 승인 Evidence (2/3 — 조건 미충족)

Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273) Gold 승인에서 정한 기준(구두 합의·Issue 코멘트가 아닌 GitHub review ID·actor·submitted timestamp·commit OID를 immutable evidence로 수집)을 그대로 적용한다. 원본은 [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json)에 보관한다.

| 리뷰어 | 역할 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | Safety·제품 수용 | `APPROVED` | [`5155621419`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155621419) | `2026-09-09T14:21:39Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |
| 송은영 (`@phina-io`) | Backend·공개 DTO | `APPROVED` | [`5155878658`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155878658) | `2026-09-09T14:41:52Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |

**위 두 승인에 한한 최신 HEAD 조건**: 두 승인의 대상 commit `3d63fc5a`는 PR #382의 최종 `headRefOid`와 동일하며, 최종 승인(`14:41:52Z`) 이후 추가 커밋이 없다. 즉 이 2인의 승인은 최신 HEAD 기준으로 유효하며 stale하지 않다. 다만 이는 **2인 승인의 유효성**에 관한 것이고, 3인 승인 요건 자체의 충족을 의미하지 않는다. 병합은 `2026-09-09T16:50:53Z`, merge commit `7d79c6b1720c2b5e06cecda0ccd38526899406f6`이다.

**자동화 검증 충족**: 승인 대상 commit `3d63fc5a`의 check run `test`·`lint`·`frontend` 모두 `success`.

### 미충족 항목 — 남한솔 (`@solia142`) 책임 리뷰

남한솔 (`@solia142`) — 환자 표시·오류 UX 책임 리뷰의 승인 evidence가 **없다**. PR #382 리뷰, PR 코멘트, Issue #173 코멘트 어디에도 참여 기록이 없다. 더 나아가 **`@solia142`는 PR #382의 리뷰어로 지정된 적이 없다** — 병합 시점 `requested_reviewers`가 비어 있었고 리뷰 요청 이력도 없다. 따라서 본 Decision이 명시한 3인 승인 조건은 충족되지 않았고, 상태는 `Proposed`로 유지한다.

> **핸들 정정**: 본 문서와 제안 계약은 최초 작성 시 남한솔의 GitHub 핸들을 `@ansol-nam`으로 기재했으나, 해당 계정은 GitHub에 존재하지 않으며(`GET /users/ansol-nam` → 404) 저장소 collaborator도 아니다. 저장소의 다른 문서(예: `docs/designs/issue-144-optional-review-fields-implementation-plan.md`)가 기록한 실제 핸들 `@solia142`로 정정했다. 잘못된 핸들 때문에 리뷰 요청이 전달되지 않았으며, 이는 **승인 조건이 충족되지 않은 원인**이지 해당 리뷰를 면제할 근거가 아니다.

**승인 조건 변경 절차**: 본 Decision의 지정 책임 리뷰어 구성을 바꾸려면, 구현 작성자가 상태 전이 PR에서 소급 처리하지 않고 세 영향 영역(Safety·제품 수용, Backend·공개 DTO, 환자 표시·오류 UX)의 책임 리뷰어가 승인한 별도 Decision 또는 개정 절차를 먼저 거쳐야 한다. 저장소 AGENTS 규칙이 공유 경계가 불명확할 때 값을 추정하지 말고 owner와 조정하도록 요구하는 것과 같은 취지다.

### `Approved` 전이를 위해 남은 절차

**PR #382에서의 소급 승인은 경로가 아니다.** 본 조건은 병합 전 게이트이고 #382는 이미 병합되었으므로, 지금 #382에 승인을 추가하는 방식으로는 조건을 충족할 수 없다. 그렇게 수집한 서명은 게이트 통과의 evidence가 아니라 게이트를 놓친 뒤의 사후 서명이며, Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273)에서 정한 immutable evidence 기준과 맞지 않는다.

따라서 남은 절차는 위 "승인 조건 변경 절차"에 따른 **별도 Decision·개정** 하나다. 해당 개정 문서는 최소한 다음을 다뤄야 한다.

- 병합 전 게이트가 충족되지 않은 채 PR #382가 병합된 사실의 기록
- 놓친 책임 리뷰(환자 표시·오류 UX)를 어떻게 처리할지 — 사후 검토로 갈음할지, 조건을 다시 정의할지, 별도 검증을 붙일지
- 개정 후의 승인 조건과 그 충족 방법

이 판단은 세 영향 영역(Safety·제품 수용, Backend·공개 DTO, 환자 표시·오류 UX) 책임 리뷰어의 몫이며, 구현 작성자가 단독으로 정하지 않는다. 개정이 승인된 뒤에야 상태를 전이하고, 전이 시점에 제안 계약의 상태 디렉터리 정합(`proposed/` 유지 여부 또는 `targets/` 편입)도 함께 정렬한다.
