# Product Decision: PD-173 승인 게이트 미충족 병합 처리 및 승인 조건 개정

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-173-20260910` |
| 상태 | Proposed · Review pending — 지정 책임 리뷰어 3인 승인 필요 |
| 제안일 | 2026-09-10 |
| 개정 대상 | [`PD-173-20260909`](./2026-09-09-rag-preflight-compound-stale-priority.md) 「승인 및 적용 조건」 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용, 송은영 (`@phina-io`) — Backend·공개 DTO, 남한솔 (`@solia142`) — 환자 표시·오류 UX |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · 선행 [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) · 현황 기록 [PR #405](https://github.com/AI-HealthCare-05/AH_05_04/pull/405) |
| 관련 계약 | [`safety-result-compound-stale-priority-v1.md`](../../contracts/proposed/post-mvp-1/safety-result-compound-stale-priority-v1.md) |
| 승인 현황 Evidence | [`docs/validation/rag/issue-173/decision-approval-evidence.json`](../../validation/rag/issue-173/decision-approval-evidence.json) |

## 목적

`PD-173-20260909`의 승인 조건은 **병합 전 게이트**였으나 충족되지 않은 채 PR #382가 병합되었다. 이 사실을 기록하고, 놓친 책임 리뷰를 어떻게 처리할지와 개정 후의 승인 조건을 확정한다. 본 문서가 승인되기 전까지 `PD-173-20260909`와 제안 계약은 `Proposed`로 유지된다.

## 배경 — 확인된 사실

모든 항목은 GitHub API 원본 조회로 확인했다. 원본은 승인 현황 Evidence JSON에 보관한다.

`PD-173-20260909` 「승인 및 적용 조건」의 문언:

> PR #382의 최신 HEAD에서 지정 책임 리뷰어(권가빈, 송은영, 남한솔)의 승인을 받고 자동화된 계약/단위 테스트가 통과해야 본 Decision과 제안 계약이 승인된 Target으로 편입될 수 있다.

| 항목 | 확인 결과 |
| --- | --- |
| PR #382 최종 HEAD | `3d63fc5a59a4ce4d66b0bb0ae5d16742e9c0fc3d` |
| 권가빈 (`@hazelnutflavoured`) | `APPROVED` · review [`5155621419`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155621419) · `2026-09-09T14:21:39Z` · 대상 `3d63fc5a` |
| 송은영 (`@phina-io`) | `APPROVED` · review [`5155878658`](https://github.com/AI-HealthCare-05/AH_05_04/pull/382#pullrequestreview-5155878658) · `2026-09-09T14:41:52Z` · 대상 `3d63fc5a` |
| 남한솔 (`@solia142`) | **승인 없음.** 리뷰·코멘트 기록 없음. PR #382의 리뷰어로 **지정된 적 없음**(`requested_reviewers` 비어 있음, 리뷰 요청 이력 없음) |
| 자동화 검증 | `3d63fc5a`의 check run `test`·`lint`·`frontend` 모두 `success` |
| 병합 | `2026-09-09T16:50:53Z` · merge commit `7d79c6b1720c2b5e06cecda0ccd38526899406f6` · `@ceohwj` |

### 리뷰 요청이 전달되지 않은 원인

`PD-173-20260909`와 제안 계약은 남한솔의 GitHub 핸들을 `@ansol-nam`으로 기재했으나 이 계정은 존재하지 않는다(`GET /users/ansol-nam` → 404, 저장소 collaborator 아님). 저장소의 다른 문서가 기록한 실제 핸들은 `@solia142`다. 잘못된 핸들로는 리뷰 요청이 성립하지 않으므로 해당 책임 리뷰는 요청 경로 자체가 없었다. 핸들은 PR #405에서 정정했다.

이는 조건 미충족의 **원인**이며, 해당 책임 리뷰를 면제할 근거가 아니다.

### 소급 충족이 불가능한 이유

본 조건은 병합 전 게이트다. PR #382는 이미 병합되었고, 게이트를 통과하지 못한 채 병합되었다는 사실은 사후에 되돌릴 수 없다. 지금 #382에 승인을 추가하더라도 "병합 전 3인 승인을 받았다"는 evidence가 아니라 게이트를 놓친 뒤의 사후 서명이며, Issue [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273)에서 정한 immutable evidence 기준과 맞지 않는다. 따라서 조건의 소급 충족이 아니라 **조건의 개정**으로 처리한다.

### 현재 노출 범위 — 환자 영향 없음

PR #382가 병합한 판정 kernel `rag_runtime/identification_preflight.py`는 **프로덕션 호출부가 없다**. `identification_preflight`를 import하는 코드는 `tests/contract/rag/test_preflight_decision_contract.py`와 `ai_worker/tests/rag/test_identification_preflight.py` 뿐이며, 런타임 경로에 연결되어 있지 않다.

즉 게이트를 놓친 채 병합되었으나 **환자에게 노출된 `fallback_code`는 없다**. 이 항목은 본 개정의 시급성 판단 근거이며, 놓친 게이트를 정당화하지 않는다.

## 결정 제안

> 아래는 구현 작성자(`@ceohwj`)의 제안이며 확정이 아니다. 세 영향 영역 책임 리뷰어의 승인 전까지 어떤 항목도 효력이 없다. 특히 제안 2는 남한솔 (`@solia142`)의 책임 범위에 대한 제안이므로 본인 판단이 우선한다.

### 제안 1. 게이트 미충족 병합 사실의 확정 기록

PR #382가 `PD-173-20260909`의 병합 전 승인 게이트를 충족하지 않은 채 병합되었음을 확정 사실로 기록한다. 이 기록은 철회하거나 사후 승인으로 대체하지 않는다.

### 제안 2. 놓친 책임 리뷰의 처리 — 사후 검토로 갈음

남한솔 (`@solia142`)의 환자 표시·오류 UX 책임 리뷰를 **면제하지 않고**, 본 개정 PR에서 현 시점 콘텐츠에 대한 검토·승인으로 수행한다. 3인 승인 요건 자체는 유지한다.

근거: 놓친 것은 리뷰의 *시점*이지 리뷰의 *필요성*이 아니다. 확정 대상인 공개 `fallback_code` 사영 우선순위는 지금도 검토 가능한 상태로 문서에 남아 있고, kernel이 런타임에 연결되지 않아 검토 결과를 반영할 여지도 남아 있다.

대안(승인 시 본 제안을 대체):

- **2-a. 조건 재정의** — 환자 표시·오류 UX 리뷰를 `PD-173` 승인 요건에서 제외하고 후속 구현 게이트로 이관한다. 세 리뷰어 전원 동의가 필요하며, `@solia142`가 [PR #405 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/405)에서 이 방향에 동의하기 어렵다고 밝혔으므로 본 문서는 이를 기본안으로 제안하지 않는다.
- **2-b. 별도 검증 부가** — 사후 검토에 더해 환자 표시 문구·오류 UX에 대한 계약 테스트 또는 별도 검증 산출물을 추가 요건으로 건다.

### 제안 3. 개정 후 승인 조건

`PD-173-20260909` 「승인 및 적용 조건」을 다음으로 대체한다.

> `PD-173-20260909`와 제안 계약 `safety-result-compound-stale-priority-v1`은 **본 개정 PR의 최신 HEAD**에서 지정 책임 리뷰어 3인(권가빈 `@hazelnutflavoured`, 송은영 `@phina-io`, 남한솔 `@solia142`) 전원의 `APPROVED` 리뷰를 받고 자동화된 계약/단위 테스트가 통과해야 `Approved`로 전이할 수 있다. 승인 evidence는 #273 기준(review ID·actor·submitted timestamp·commit OID)으로 수집한다.

「최신 HEAD」의 판정은 `PD-173-20260909`와 동일하게 유지한다 — 최종 승인 이후 추가 커밋이 없어야 하며, 추가 커밋이 발생하면 승인은 stale로 간주하고 재승인을 받는다.

### 제안 4. 상태 전이 시 디렉터리 정합

`Approved` 전이 시 제안 계약의 상태 디렉터리 정합을 함께 정렬한다. `docs/contracts/proposed/`에 유지하면 문서 상태도 `Proposed`로 두고, `Approved`로 전이하려면 같은 PR에서 `docs/contracts/targets/post-mvp-1/`로 이동하고 인덱스를 함께 갱신한다. 저장소 governance가 상태 디렉터리를 계약 상태의 기준으로 두는 규칙([`post-mvp-1-document-authority.md`](../post-mvp-1-document-authority.md) 3항)을 따른다.

### 제안 5. 재발 방지

1. Decision·계약 문서에 기재하는 책임 리뷰어 GitHub 핸들은 실재 계정이고 저장소 collaborator인지 확인한 뒤 기재한다.
2. 병합 전 승인 게이트를 건 Decision은 해당 PR에 지정 책임 리뷰어 전원을 실제 reviewer로 등록한다.
3. 게이트를 건 Decision의 대상 PR을 병합하기 전에 지정 리뷰어 전원의 `APPROVED` 유무를 확인한다.

## 승인 및 적용 조건

본 문서는 `Proposed` 상태다. 지정 책임 리뷰어 3인 전원이 본 개정 PR의 최신 HEAD에서 `APPROVED`를 제출하고 자동화된 계약/단위 테스트가 통과해야 본 개정이 효력을 갖는다. 본 개정이 승인된 뒤에야 `PD-173-20260909`와 제안 계약의 상태를 전이할 수 있으며, 전이는 별도 PR에서 수행한다.

본 개정의 승인 evidence도 Issue #273 기준(review ID·actor·submitted timestamp·commit OID)으로 수집하여 `docs/validation/rag/issue-173/`에 기록한다.
