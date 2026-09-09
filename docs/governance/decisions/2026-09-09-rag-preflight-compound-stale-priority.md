# Product Decision: RAG Preflight 복합 STALE 우선순위 및 단일 오류 사영 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-173-20260909` |
| 상태 | Proposed · Review pending — PR #382 병합 전 승인 필요 |
| 제안일 | 2026-09-09 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Safety·제품 수용, 송은영 (`@phina-io`) — Backend·공개 DTO, 남한솔 (`@ansol-nam`) — 환자 표시·오류 UX |
| 추적 Issue·PR | [#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173) · [PR #382](https://github.com/AI-HealthCare-05/AH_05_04/pull/382) |
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
