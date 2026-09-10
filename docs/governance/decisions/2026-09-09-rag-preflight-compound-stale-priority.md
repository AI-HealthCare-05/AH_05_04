# Product Decision: RAG Preflight 복합 STALE 우선순위 및 단일 오류 사영 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-173-20260909` |
| 상태 | **Approved** — 개정 `PD-173-20260910`에 따라 PR #405 최신 HEAD에서 책임 리뷰어 3인 전원 승인 (2026-09-10) |
| 승인일 | 2026-09-10 (최종 승인 `2026-09-10T02:15:06Z`) |
| 적용 개정 | [`PD-173-20260910`](./2026-09-10-rag-preflight-compound-stale-approval-gate-amendment.md) — 병합 전 게이트 미충족 처리 및 승인 조건 개정 (Approved) |
| 제안일 | 2026-09-09 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용 `APPROVED`, 송은영 (`@phina-io`) — Backend·공개 DTO `APPROVED`, 남한솔 (`@solia142`) — 환자 표시·오류 UX `APPROVED` |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) (`MERGED` `2026-09-09T16:50:53Z`) |
| 승인 현황 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json) |
| 상위 계약 | [`docs/contracts/targets/post-mvp-1/safety-result-v2.md`](../../contracts/targets/post-mvp-1/safety-result-v2.md) |
| 확정 계약 | [`docs/contracts/targets/post-mvp-1/safety-result-compound-stale-priority-v1.md`](../../contracts/targets/post-mvp-1/safety-result-compound-stale-priority-v1.md) — `Approved Target · Not implemented` |

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

본 Decision은 개정 [`PD-173-20260910`](./2026-09-10-rag-preflight-compound-stale-approval-gate-amendment.md)이 정한 조건에 따라 **`Approved`** 다. 지정 책임 리뷰어 3인 전원이 개정 PR [#405](https://github.com/AI-HealthCare-05/AH_05_04/pull/405)의 최신 HEAD `0649e466`에서 `APPROVED`를 제출했고 자동화 검증이 통과했다.

`Approved`는 본 Decision과 확정 계약의 어휘·우선순위 규칙이 확정되었음을 의미하며, Current Runtime이나 Production 활성화를 의미하지 않는다. 판정 kernel `rag_runtime/identification_preflight.py`는 프로덕션 호출부가 없다.

### 승인 Evidence

Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273) Gold 승인에서 정한 기준(구두 합의·Issue 코멘트가 아닌 GitHub review ID·actor·submitted timestamp·commit OID를 immutable evidence로 수집)을 적용한다. 원본은 [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json)에 보관한다.

| 리뷰어 | 역할 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- | --- |
| 남한솔 (`@solia142`) | 환자 표시·오류 UX | `APPROVED` | [`5161920880`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161920880) | `2026-09-10T02:09:13Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |
| 권가빈 (`@hazelnutflavoured`) | Safety·제품 수용 | `APPROVED` | [`5161932826`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161932826) | `2026-09-10T02:10:43Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |
| 송은영 (`@phina-io`) | Backend·공개 DTO | `APPROVED` | [`5161953360`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161953360) | `2026-09-10T02:15:06Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |

세 승인 모두 PR #405의 최종 `headRefOid` `0649e466`을 대상으로 하며 최종 승인 이후 추가 커밋이 없다. 해당 commit의 check run `test`·`lint`·`frontend` 모두 `success`. PR #405 병합은 `2026-09-10T02:19:42Z`, merge commit `548468763a21233860b06deb8bea7e24a8900950`이다.

### 이력 — 최초 승인 조건과 그 미충족

본 Decision이 최초에 건 조건은 다음과 같은 **병합 전 게이트**였다.

> PR #382의 최신 HEAD에서 지정 책임 리뷰어(권가빈, 송은영, 남한솔)의 승인을 받고 자동화된 계약/단위 테스트가 통과해야 본 Decision과 제안 계약이 승인된 Target으로 편입될 수 있다.

이 게이트는 **충족되지 않은 채 PR #382가 병합되었다.** 이 사실은 개정으로 소급 치유되지 않으며 확정 기록으로 남긴다.

| 항목 | 값 |
| --- | --- |
| 권가빈 (`@hazelnutflavoured`) | `APPROVED` · review [`5155621419`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155621419) · `2026-09-09T14:21:39Z` · 대상 `3d63fc5a` |
| 송은영 (`@phina-io`) | `APPROVED` · review [`5155878658`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155878658) · `2026-09-09T14:41:52Z` · 대상 `3d63fc5a` |
| 남한솔 (`@solia142`) | **승인 없음.** PR #382의 리뷰어로 지정된 적 없음 (`requested_reviewers` 비어 있음) |
| 병합 | `2026-09-09T16:50:53Z` · merge commit `7d79c6b1720c2b5e06cecda0ccd38526899406f6` |

원인: 본 문서와 계약이 최초 작성 시 남한솔의 GitHub 핸들을 `@ansol-nam`으로 기재했으나 해당 계정은 존재하지 않는다(`GET /users/ansol-nam` → 404, 저장소 collaborator 아님). 실제 핸들은 `@solia142`이며 [PR #405](https://github.com/AI-HealthCare-05/AH_05_04/pull/405)에서 정정했다. 잘못된 핸들로는 리뷰 요청이 성립하지 않아 해당 책임 리뷰는 요청 경로 자체가 없었다.

병합 전 게이트는 사후에 되돌릴 수 없으므로 #382에 승인을 추가하는 방식으로 조건을 소급 충족시키지 않았다. 대신 개정 `PD-173-20260910`으로 승인 조건을 개정하고, 놓친 책임 리뷰를 면제하지 않은 채 3인 승인 요건을 유지하여 개정 PR에서 수행했다. 재발 방지 조치는 개정 문서에 기록되어 있다.