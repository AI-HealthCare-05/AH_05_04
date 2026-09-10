# Product Decision: RAG Preflight 복합 STALE 우선순위 및 단일 오류 사영 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-173-20260909` |
| 상태 | **Approved** — PR #382 최신 HEAD에서 책임 리뷰 승인 완료 (2026-09-09) |
| 제안일 | 2026-09-09 |
| 승인일 | 2026-09-09 (최종 승인 `2026-09-09T14:41:52Z`) |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용 `APPROVED`, 송은영 (`@phina-io`) — Backend·공개 DTO `APPROVED` |
| 범위 제외 리뷰 | 남한솔 (`@ansol-nam`) — 환자 표시·오류 UX (본 Decision 승인 요건에서 제외, [사유](#책임-리뷰-범위와-제외-근거)) |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) (`MERGED` `2026-09-09T16:50:53Z`) |
| 승인 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json) |
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

본 Decision은 PR #382의 최신 HEAD에서 지정 책임 리뷰어의 승인을 받고 자동화된 계약/단위 테스트가 통과할 것을 적용 조건으로 했다. 아래 immutable evidence로 두 조건의 충족을 확인하여 상태를 `Approved`로 전이한다.

`Approved`는 본 Decision과 제안 계약의 어휘·우선순위 규칙이 확정되었음을 의미하며, 그 자체로 Production 활성화를 의미하지 않는다. 런타임 활성화는 별도 릴리즈 절차를 따른다.

### 승인 Evidence

Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273) Gold 승인에서 정한 기준(구두 합의·Issue 코멘트가 아닌 GitHub review ID·actor·submitted timestamp·commit OID를 immutable evidence로 수집)을 그대로 적용한다. 원본은 [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json)에 보관한다.

| 리뷰어 | 역할 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | Safety·제품 수용 | `APPROVED` | [`5155621419`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155621419) | `2026-09-09T14:21:39Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |
| 송은영 (`@phina-io`) | Backend·공개 DTO | `APPROVED` | [`5155878658`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155878658) | `2026-09-09T14:41:52Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |

**최신 HEAD 조건 충족**: 두 승인의 대상 commit `3d63fc5a`는 PR #382의 최종 `headRefOid`와 동일하며, 최종 승인(`14:41:52Z`) 이후 추가 커밋이 없다. 따라서 "PR #382의 최신 HEAD에서 승인" 조건이 문언 그대로 충족된다. 병합은 `2026-09-09T16:50:53Z`, merge commit `7d79c6b1720c2b5e06cecda0ccd38526899406f6`이다.

**자동화 검증 충족**: 승인 대상 commit `3d63fc5a`의 check run `test`·`lint`·`frontend` 모두 `success`.

### 책임 리뷰 범위와 제외 근거

남한솔 (`@ansol-nam`) — 환자 표시·오류 UX 리뷰는 **본 Decision의 승인 필수 요건에서 제외**한다. PR #382 리뷰, PR 코멘트, Issue #173 코멘트 어디에도 참여 기록이 없으며 병합 시점 리뷰 요청도 남아 있지 않다.

제외 근거: PD-173이 확정하는 대상은 복합 STALE 상황의 공개 `fallback_code` 사영 **우선순위**와 내부 `stale_reason` 분리 규칙이다. 사영 결과로 선택되는 공개 코드는 이미 승인된 `safety-result-v2.md`의 9개 코드 집합 내부이며 본 Decision은 신규 공개 코드나 환자 대면 문구를 추가하지 않는다. 환자 표시 문구·오류 UX 렌더링은 본 Decision이 규정하지 않는 후속 구현 범위다.

후속 요건: 환자 표시 문구 및 오류 UX 구현 시점에는 남한솔 (`@ansol-nam`) 책임 리뷰를 별도 승인 요건으로 유지한다.
