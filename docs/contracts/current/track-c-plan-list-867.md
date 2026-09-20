# Track C ActionPlan 목록 조회 v1 — #867

- 상태: Current — PR #873의 `GET /api/v1/support-action-plans` 구현과 함께 반영하는 런타임 계약.
- 구현: 송은영 (`phina-io`). 단일 책임 리뷰어: 권가빈 (`hazelnutflavoured`).
- 검토 범위: Track C Backend 목록 API·DTO·SELF 소유권·Frontend 소비 경계.
- 근거: [PD-867](../../governance/decisions/2026-09-20-track-c-plan-list-867.md), [ActionPlan lifecycle v1](track-c-plan-lifecycle-617.md).

## 범위

최신 런타임은 ActionPlan 생성, 단건 상세 조회, 완료/취소, follow-up을 이미 제공한다. 이 계약은 저장된 plan id를 앱 재진입 후 다시 찾기 위한 목록 조회만 추가한다.

이 계약은 다음을 변경하지 않는다.

- `POST /api/v1/support-action-plans` 생성 계약
- `GET /api/v1/support-action-plans/{id}` 상세 snapshot 계약
- `PATCH /api/v1/support-action-plans/{id}` 완료/취소 의미
- follow-up 계약
- DB schema와 migration
- Frontend 화면 구현
- Track C 외부 공개 또는 Production 승인

## GET /api/v1/support-action-plans

- operationId: `support-action-plan.list`.
- 인증 필요. 미인증은 기존 공통 401을 반환한다.
- Plan → Barrier → Check-in → occurrence → schedule → prescription의 SELF 소유권으로 조회 범위를 제한한다.
- 타 사용자 plan은 목록에 나타나지 않는다. 타 사용자 plan 존재 여부도 노출하지 않는다.
- 행 잠금, idempotency key, write, 활성 config 파일 로딩을 수행하지 않는다.
- 모든 성공 응답은 `Cache-Control: no-store`를 따른다.

### 응답

성공 200 응답은 `SupportActionPlanListResponse`다.

```json
{
  "data": [
    {
      "support_action_plan_id": "00000000-0000-0000-0000-000000000000",
      "support_code": "REMINDER_SETUP",
      "status": "ACTIVE",
      "created_at": "2026-09-20T00:00:00Z",
      "completed_at": null,
      "cancelled_at": null
    }
  ]
}
```

필드 계약:

| 필드 | required | nullable | 의미 |
| --- | --- | --- | --- |
| `support_action_plan_id` | yes | no | 기존 상세 화면으로 이동할 plan id |
| `support_code` | yes | no | 기존 `SupportCode` enum |
| `status` | yes | no | `ACTIVE`, `COMPLETED`, `CANCELLED` |
| `created_at` | yes | no | plan 생성 시각 |
| `completed_at` | yes | yes | 완료 시각. ACTIVE/CANCELLED에서는 null |
| `cancelled_at` | yes | yes | 취소 시각. ACTIVE/COMPLETED에서는 null |

목록 응답은 `action_config_snapshot`, `barrier_response_id`, `rule_version`, `copy_version`, 처방 원문, 환자정보, 내부 계산값을 반환하지 않는다. 상세 snapshot은 기존 `GET /api/v1/support-action-plans/{id}`에서만 반환한다.

### 정렬

정렬은 `created_at DESC, id DESC`다. 동일 생성 시각 row가 있어도 안정적으로 재조회할 수 있다.

### 오류

| HTTP | code |
| --- | --- |
| 401 | 기존 인증 오류 |
| 422 | VALIDATION_FAILED |

오류는 기존 `code`, `message`, `details`, `trace_id` envelope를 따른다.

## Frontend 소비 경계

Frontend는 `/track-c/plans` 목록 화면과 메뉴 진입점을 후속 PR에서 연결한다. 목록 item 클릭 시 기존 `/track-c/plans/{planId}` 상세 화면으로 이동한다. `완료 확인하기` 버튼은 조회가 아니라 기존 완료 처리 의미를 유지한다.

검증: PR #873의 Track C API/lifecycle 테스트.
