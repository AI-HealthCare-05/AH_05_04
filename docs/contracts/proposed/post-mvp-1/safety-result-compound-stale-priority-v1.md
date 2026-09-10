# Safety Result 복합 STALE 우선순위 계약 제안 v1 (#173)

| 항목 | 값 |
| --- | --- |
| 문서 상태 | **Approved** — PR #382 최신 HEAD에서 책임 리뷰 승인 완료 (2026-09-09) |
| 제안일 | 2026-09-09 |
| 승인일 | 2026-09-09 (최종 승인 `2026-09-09T14:41:52Z`) |
| 작성·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용 `APPROVED`, 송은영 (`@phina-io`) — Backend·공개 DTO `APPROVED` |
| 범위 제외 리뷰 | 남한솔 (`@ansol-nam`) — 환자 표시·오류 UX (승인 요건에서 제외, [근거 Decision 참조](../../../governance/decisions/2026-09-09-rag-preflight-compound-stale-priority.md#책임-리뷰-범위와-제외-근거)) |
| 근거 Decision | [`PD-173-20260909`](../../../governance/decisions/2026-09-09-rag-preflight-compound-stale-priority.md) |
| 상위 목표 계약 | [`docs/contracts/targets/post-mvp-1/safety-result-v2.md`](../../targets/post-mvp-1/safety-result-v2.md) |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) (`MERGED` `2026-09-09T16:50:53Z`) |
| 승인 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../../validation/rag/issue-173/decision-approval-evidence.json) |

## 목적과 적용 범위

이 문서는 [Safety Result·Citation 계약 v2](../../targets/post-mvp-1/safety-result-v2.md)가 정의하는 단일 원인별 STALE 사영을 확장하여, 복합 STALE 상황(둘 이상의 STALE 원인이 동시 발생한 경우)에서 downstream 단일 공개 `fallback_code` 및 내부 `stale_reason`으로 투영하는 결정적 우선순위를 정의하는 제안 계약이다.

본 문서는 근거 Decision `PD-173-20260909`와 함께 지정 책임 리뷰어의 승인을 받아 `Approved` 상태다 (승인 evidence는 아래 참조). 다만 문서 위치는 여전히 `docs/contracts/proposed/`이며, 상위 Target 계약(`safety-result-v2.md`)으로 병합되기 전까지는 확정된 Current 런타임 동작으로 간주하지 않는다.

### 승인 Evidence

| 리뷰어 | 역할 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | Safety·제품 수용 | `APPROVED` | [`5155621419`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155621419) | `2026-09-09T14:21:39Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |
| 송은영 (`@phina-io`) | Backend·공개 DTO | `APPROVED` | [`5155878658`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155878658) | `2026-09-09T14:41:52Z` | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |

두 승인의 대상 commit `3d63fc5a`는 PR #382의 최종 `headRefOid`이며 최종 승인 이후 추가 커밋이 없다. 해당 commit의 check run `test`·`lint`·`frontend` 모두 `success`. 원본 evidence는 [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../../validation/rag/issue-173/decision-approval-evidence.json)에 보관한다.

남한솔 (`@ansol-nam`) — 환자 표시·오류 UX 리뷰는 본 계약 승인 요건에서 제외했다. 본 계약은 이미 승인된 `safety-result-v2.md`의 9개 공개 코드 집합 내부에서 사영 우선순위만 정의하고 신규 공개 코드나 환자 대면 문구를 추가하지 않기 때문이다. 환자 표시 문구·오류 UX 구현 시에는 해당 책임 리뷰가 별도 승인 요건으로 유지된다. 상세 근거는 [PD-173 § 책임 리뷰 범위와 제외 근거](../../../governance/decisions/2026-09-09-rag-preflight-compound-stale-priority.md#책임-리뷰-범위와-제외-근거)를 참조한다.

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
