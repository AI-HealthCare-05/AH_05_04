# Safety Result 복합 STALE 우선순위 계약 v1 (#173)

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target · Not implemented — 판정 kernel은 병합되었으나 런타임 호출부 없음 |
| 제안일 | 2026-09-09 |
| 승인일 | 2026-09-10 (최종 승인 `2026-09-10T02:15:06Z`) |
| 작성·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용 `APPROVED`, 송은영 (`@phina-io`) — Backend·공개 DTO `APPROVED`, 남한솔 (`@solia142`) — 환자 표시·오류 UX `APPROVED` |
| 근거 Decision | [`PD-173-20260909`](../../../governance/decisions/2026-09-09-rag-preflight-compound-stale-priority.md) · 개정 [`PD-173-20260910`](../../../governance/decisions/2026-09-10-rag-preflight-compound-stale-approval-gate-amendment.md) |
| 상위 목표 계약 | [Safety Result·Citation 계약 v2](./safety-result-v2.md) |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) · [PR #405](https://github.com/AI-HealthCare-05/AH_05_04/pull/405) |
| 승인 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../../validation/rag/issue-173/decision-approval-evidence.json) |
| Last verified | 2026-09-10 |

## 목적과 적용 범위

이 문서는 [Safety Result·Citation 계약 v2](./safety-result-v2.md)가 정의하는 단일 원인별 STALE 사영을 확장하여, 복합 STALE 상황(둘 이상의 STALE 원인이 동시 발생한 경우)에서 downstream 단일 공개 `fallback_code` 및 내부 `stale_reason`으로 투영하는 결정적 우선순위를 정의한다.

본 문서는 `Approved Target`이며 **Current Runtime이 아니다.** 판정 kernel `rag_runtime/identification_preflight.py`는 병합되었으나 프로덕션 호출부가 없고 테스트에서만 참조된다. `current/`로의 승격은 관련 구현·migration·OpenAPI/DTO·계약 통합 테스트와 실행 증빙을 포함한 구현 PR에서 지정 리뷰어 승인을 받은 뒤에 한다.

### 승인 Evidence

지정 책임 리뷰어 3인 전원이 개정 Decision [`PD-173-20260910`](../../../governance/decisions/2026-09-10-rag-preflight-compound-stale-approval-gate-amendment.md)을 담은 [PR #405](https://github.com/AI-HealthCare-05/AH_05_04/pull/405)의 최신 HEAD `0649e466`에서 승인했다. Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273) 기준(review ID·actor·submitted timestamp·commit OID)으로 수집했으며 원본은 [`decision-approval-evidence.json`](../../../validation/rag/issue-173/decision-approval-evidence.json)에 보관한다.

| 리뷰어 | 역할 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- | --- |
| 남한솔 (`@solia142`) | 환자 표시·오류 UX | `APPROVED` | [`5161920880`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161920880) | `2026-09-10T02:09:13Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |
| 권가빈 (`@hazelnutflavoured`) | Safety·제품 수용 | `APPROVED` | [`5161932826`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161932826) | `2026-09-10T02:10:43Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |
| 송은영 (`@phina-io`) | Backend·공개 DTO | `APPROVED` | [`5161953360`](https://github.com/AI-HealthCare-05/AH_05_04/pull/405#pullrequestreview-5161953360) | `2026-09-10T02:15:06Z` | `0649e466e4e9c120a13b932342fefa98090279dd` |

세 승인 모두 PR #405의 최종 `headRefOid` `0649e466`을 대상으로 하며 최종 승인 이후 추가 커밋이 없다. 해당 commit의 check run `test`·`lint`·`frontend` 모두 `success`. PR #405는 `2026-09-10T02:19:42Z`에 병합되었다(merge commit `548468763a21233860b06deb8bea7e24a8900950`).

선행 PR #382에서 수집한 2인 승인과 그 게이트가 충족되지 않은 채 병합된 경위는 [`PD-173-20260909` § 승인 및 적용 조건](../../../governance/decisions/2026-09-09-rag-preflight-compound-stale-priority.md#승인-및-적용-조건)과 개정 Decision에 기록되어 있다. 본 계약의 승인 근거는 위 3인 승인이다.

## 복합 STALE 우선순위와 단일 오류 사영

처방 Version과 실행 Context(Identification Snapshot, Runtime Bundle 등)의 불일치가 동시에 발생한 경우, 다음 우선순위에 따라 단일 공개 `fallback_code` 및 내부 `stale_reason`으로 사영한다.

1. `PRESCRIPTION_STALE` (최우선): 처방 Version 불일치는 환자 임상 원천 데이터(처방전) 자체의 변경이므로, 내부 실행 Context 불일치보다 환자 공개 오류로 우선한다 (공개 `PRESCRIPTION_STALE`, 내부 `stale_reason=None`).
2. `IDENTIFICATION_STALE`: 처방 버전 불일치가 없을 때, 약제 단위의 공식 의약품 식별 불일치는 런타임 릴리즈 번들 변경보다 상위 도메인 사유로 우선한다 (공개 `EXECUTION_CONTEXT_STALE`, 내부 `stale_reason="IDENTIFICATION_STALE"`).
3. `RUNTIME_RELEASE_STALE`: 활성 런타임 릴리즈 번들 불일치 (공개 `EXECUTION_CONTEXT_STALE`, 내부 `stale_reason="RUNTIME_RELEASE_STALE"`).

## 공통 불변식

- 모든 Context 불일치 종결은 `AI_JOB=STALE + release_decision=STALE + is_current=false`다.
- 내부 상세 사유(`stale_reason`)는 공개 DTO에 노출하지 않는다.
- 공개 `fallback_code`는 `safety-result-v2.md`가 허용한 9개 코드(`NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `EXECUTION_CONTEXT_STALE`, `UNSUPPORTED_REQUEST`) 안에서만 선택한다.

## 최소 Contract Test

- 복합 STALE 발생 시 단일 fallback_code 사영 우선순위 (`PRESCRIPTION_STALE` > `IDENTIFICATION_STALE` > `RUNTIME_RELEASE_STALE`) exact-match
- `PD-173-20260909` 상위 Decision 및 `safety-result-v2.md` 목표 계약과의 어휘 일치
