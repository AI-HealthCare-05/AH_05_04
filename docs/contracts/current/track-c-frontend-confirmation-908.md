# Track C Frontend C-03~C-06 사용자 확인 흐름 — #908

- 상태: PR #908 구현·자동 테스트와 함께 Current 승격 리뷰 대상.
- Decision: [PD-908](../../governance/decisions/2026-09-21-track-c-frontend-confirmation-908.md)
- 구현 담당: 남한솔 (`@solia142`)
- 단일 책임 리뷰어: 권가빈 (`@hazelnutflavoured`)

## 흐름

`Offer → Configure → Confirm → Plan Review → Plan 생성`

1. **C-03 Offer**: Backend가 반환한 Support만 표시·선택한다. Plan 생성 없음.
2. **C-04 Configure**: 서버가 제공한 allowlisted 질문만 local state에서 선택한다. Plan 생성 없음.
3. **C-05 Confirm**: 선택 결과와 `confirmation_prompt`를 다시 보여준다. Plan 생성 없음.
4. **C-06 Plan Review**: 최종 저장 내용을 보여준다. 최종 CTA 전 Plan 생성 없음.
5. `이 계획을 저장하고 시작하기`에서만 기존
   `POST /api/v1/support-action-plans` + `confirmed=true`를 호출한다.

## 재확인

Support 재선택은 질문과 확인 상태를 초기화한다.
질문 수정, C-05→C-04 복귀, C-06→C-05 복귀는 기존 확인을 무효화한다.
따라서 수정 이후 C-05·C-06을 다시 통과해야 하며 자동 POST는 없다.

Backend API/DTO, Support/Plan enum, Rule/Copy 내용, Safety 판단은 변경하지 않는다.
