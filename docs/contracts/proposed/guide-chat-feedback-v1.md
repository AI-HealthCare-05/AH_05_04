# Guide·Chat 피드백 v1 — #633

상태: **Proposed / 구현·검증 중**. [PD-633](../../governance/decisions/2026-09-16-guide-chat-feedback-633.md)의
로컬 구현 계약이며 책임 리뷰·병합 전이다. 책임 리뷰어는 정현우이며 영향 영역은 Backend·Frontend·Privacy·AI 평가다.

## HTTP 후보

| 메서드·경로 (`/api/v1` 기준) | 대상 |
|---|---|
| `POST /guides/{guide_id}/feedback` | 본인 SELF의 `COMPLETED` Guide |
| `POST /chat-sessions/{session_id}/messages/{message_id}/feedback` | 본인 SELF 세션에 속한 `ASSISTANT`·`COMPLETED` 메시지 |

요청은 `rating: "POSITIVE" | "NEGATIVE"` 필수, `comment: string | null` 선택이다.
`comment`는 trim 후 빈 문자열을 null로 정규화하고 정규화 후 1,000자까지 허용하는 방안을 제안한다.
추가 필드·숫자 rating·잘못된 UUID·길이 초과·NUL·잘못된 Unicode는 공통 422다. 식별자·작성자·시각·검토 결과는 요청받지 않는다.

응답 후보는 첫 저장 201, 기존 대상 재제출 200이며 공통 envelope를 따른다.

```json
{
  "data": {
    "id": "10000000-0000-4000-8000-000000000633",
    "rating": "NEGATIVE",
    "created_at": "2026-09-16T03:00:00Z",
    "updated_at": "2026-09-16T03:00:00Z"
  }
}
```

같은 정규화 값의 재전송은 id·두 시각을 보존한다. 값 변경은 기존 id·created_at을 보존하고
updated_at을 갱신한다. comment와 원본 응답은 응답에 복제하지 않는다.
읽기 endpoint·기존 Guide/Chat DTO 확장은 이번 후보에 포함하지 않는다. 따라서 새로고침 뒤 기존 선택
표시를 복원하지 않는 최소 UI다. 남한솔의 전달 의견에서 이번 범위에 GET을 추가하지 않기로 확인했다.

인증 실패는 공통 401이다. 타인·없는 대상·session/message 불일치는 동일 404다.
소유권을 확인한 대상이 미완료이거나 USER 메시지이면 409 `FEEDBACK_TARGET_NOT_READY`를 제안한다.
오류는 공통 `code/message/details/trace_id`를 따르며 원본·의견을 포함하지 않는다.
모든 성공·오류 응답은 기존 `/api/v1` no-store 정책을 따른다.

## 저장·트랜잭션 후보

`guide_feedback`, `chat_message_feedback` 두 테이블을 제안한다.
각각 `id` UUID PK, 대상 FK(`guide_id` / `chat_message_id`) NOT NULL UNIQUE,
`rating` VARCHAR(8) NOT NULL + CHECK, `comment` VARCHAR(1000) NULL,
`created_at`, `updated_at` timezone-aware NOT NULL을 가진다.
존재하지 않는 target을 허용하는 다형 `target_id`는 사용하지 않는다.
별도 user_id/profile_id를 중복 저장하지 않고 Guide 또는 ChatSession 부모 chain으로 SELF를 확인한다.

Router → Service → Repository의 단일 transaction에서 소유권과 대상 완료 상태를 확인하고,
부모 대상 row를 잠근 뒤 기존 feedback 조회·동일 값 재현·삽입 또는 갱신을 수행한다.
대상별 unique constraint와 부모 잠금으로 중복 클릭·동시 최초 요청을 직렬화한다.
실패는 전체 rollback한다. DB trigger·RLS·업무 DB 함수는 추가하지 않는다.

대상 삭제 시 피드백도 함께 삭제하는 FK CASCADE 후보를 제안한다. 계정 삭제 경로가 실제로 부모를
삭제하는지 구현 시 검증해야 하며 cascade 정의만으로 계정 삭제 이행을 주장하지 않는다.
고유 대상 FK index와 부정 검토용 `(rating, updated_at, id)` index만 둔다.
보존은 최초 created_at부터 최대 30일이다. 수정 시 연장하지 않고 만료 시 삭제한다.
만료 row에 POST하면 기존 row를 제거하고 새 id·created_at으로 201을 반환한다.
검토 목적 달성·사용자 요청 시 조기 삭제하며 연결 기록도 정리한다.

## UI·데이터 처리

완료된 결과에만 “도움이 되었나요?”와 두 rating 버튼을 표시한다. rating 선택 뒤 선택 의견 입력을 연다.
“개인정보나 처방전 원문 등 민감한 정보는 입력하지 마세요.”라는 안내를 함께 표시한다.
저장 성공 후에만 접수·선택 상태를 표시하고 평가 변경을 허용한다.
실패 시 입력한 rating/comment를 화면에 유지해 재시도할 수 있게 한다.
전송 중 중복 클릭을 막고 키보드 접근·접근성 이름을 제공한다.
의견은 로그·분석 도구·localStorage·Provider payload·저장소 artifact에 남기지 않는다.
사용자가 안내를 무시할 수 있으므로 자유 의견을 합성 또는 비민감 데이터라고 취급하지 않는다.

## 구현 PR 검증 조건

- PostgreSQL migration 단일 head, FK·unique·rating·nullability·길이 제약과 rollback 검증.
- 실제 ASGI·DB: 두 대상 성공, 동일 재전송, 갱신, 동시 최초 제출, transaction 실패 후 rollback.
- SELF 소유권, 다른 세션 message, 타인/없는 ID 동일 404, USER·미완료·실패 대상 거절.
- 인증·422·고정 오류·no-store·OpenAPI requiredness 및 comment sentinel 비로그·Provider 미호출.
- 대상 삭제와 적용 가능한 계정 삭제 후 feedback 제거 검증.
- Frontend 성공·실패·재시도·중복 클릭·키보드·작은 화면 합성 E2E.
- 검토된 합성 사례 편입 및 prompt 전후 비교 증빙은 운영 절차를 따르며 승인과 실행을 구분한다.

검증 결과는 [#633 실행 기록](../../validation/issue-633-feedback.md)을 따른다.

## 환경·삭제·운영 경계

이번 구현은 `ENV=local`만 허용하며 Staging·Production의 POST/DELETE는 404 `NOT_FOUND`다.
실제 사용자 수집·처리 근거 승인을 대체하지 않는다. Frontend는 개발 빌드에서만 표시한다.

두 POST 경로와 같은 경로의 `DELETE`는 인증과 부모 SELF 소유권을 확인한 후 feedback을 제거하고 204를 반환한다.
본인 target의 feedback이 이미 없어도 204다. 타인/없는 target·session 불일치는 동일 404다.
삭제는 target의 생성 상태에 상관없이 가능하며 의료 target 자체를 변경하지 않는다.
GET은 추가하지 않아 새로고침 시 선택 상태는 복원하지 않는다. rating을 선택하면 삭제 버튼에도 접근할 수 있다.

`python -m app.commands.purge_feedback`는 최초 생성 후 30일 이상 지난 두 테이블 row를 transaction으로 삭제한다.
출력은 삭제 개수뿐이며 Local 외 실행은 거부한다. 일일 스케줄 설치는 운영 배포 단계다.
FK cascade는 Guide/ChatMessage 직접 삭제에 적용한다. 현재 회원탈퇴 API가 없어 계정 전체 삭제 이행은 후속 검증이다.
