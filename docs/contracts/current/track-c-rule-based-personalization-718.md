# Track C 규칙 기반 개인화 지원 — #718

- 상태: Current — 이 구현 PR과 함께 반영하는 내부 런타임 계약. 책임 리뷰·병합 대기, 외부 공개 승인 아님.
- 구현 담당: 권가빈 (`@hazelnutflavoured`).
- 단일 책임 리뷰어: 김지혜 (`@Jye-rookie`).
- 결정: [PD-718](../../governance/decisions/2026-09-17-track-c-rule-based-personalization-718.md).

## 흐름

Track C는 RAG, LLM, 실시간 웹 조회를 호출하지 않는다. 사용자가 선택한 기존 6개 Barrier와 선택적 세부 이유를 승인된 정적 규칙에 대입해 지원을 0~2개 반환한다. 사용자가 지원 하나를 선택하고 명시적으로 확인해야 Plan을 만든다.

세부 이유는 서버 allowlist의 코드만 허용한다. 자유 입력 분류, 약별 효능·부작용 생성, 복용량·간격·중단·재개 제안은 하지 않는다. Safety, SELF 소유권, 최신 Check-in·Safety·Barrier, B→C 무효화와 `PUBLIC_TRACK_C` 게이트는 유지한다.

## API 변경

`GET /api/v1/barrier-responses/{id}/supports`에 선택적 `subreason_code` query를 추가한다. 응답에는 같은 `subreason_code`, 최대 2개의 `supports`, 지원별 0~5개의 고정 `questions[{question_id,text}]`를 반환한다. Barrier와 맞지 않는 세부 이유는 `422 VALIDATION_FAILED`다.

`POST /api/v1/support-action-plans`에 선택적 `subreason_code`와 `selected_question_ids`를 추가한다. 질문은 중복 없이 최대 3개이며 선택한 지원과 세부 이유에 실제로 제안된 ID만 허용한다. 서버는 현재 규칙으로 지원·세부 이유·질문을 다시 검증하고 기존 멱등 fingerprint에 새 필드를 포함한다.

`GET /api/v1/support-action-plans/{id}/resources`는 저장된 `subreason_code`와 `selected_questions`를 반환한다. 질문은 자동 전송하지 않는다.

## 저장과 버전

새 DB 컬럼이나 migration을 추가하지 않는다. `action_config_snapshot.parameters`에 `subreason_code`와 `selected_question_ids`를 저장한다. 활성 Rule/Copy는 `track-c-support-rule-2026-09-17.1`과 `track-c-support-copy-ko-2026-09-17.1`이다. 2026-09-15.1과 2026-09-16.1 파일·allowlist를 보존해 과거 Plan을 저장 당시 문구로 복원한다.

질문 문구는 코드의 승인 카탈로그에 있고 Plan에는 ID를 저장한다. 재조회는 저장된 ID를 같은 승인 카탈로그로 복원한다. 알 수 없는 ID나 임의 snapshot 필드는 fail-closed 처리한다.

## 호환성과 후속 확인

기존 소비자는 새 request 필드를 생략할 수 있다. 이 경우 세부 이유 없는 일반 제안과 빈 질문 선택으로 Plan을 만들 수 있다. 응답의 새 필드는 additive다. 기존 `travel_situation` 두 값은 같은 세부 이유 코드로 결속하며 `PREPARATION_DIFFICULT`는 일반 최대 2개 제안 경로를 사용한다.

완료 Plan의 기존 `HELPED`, `NOT_HELPED`, `NOT_SURE` Follow-up v1은 유지한다. 사용 기회·사용 여부·도움·부담을 각각 기록하는 Follow-up v2는 기존 응답을 추정 변환하지 않는 별도 migration·계약으로 진행한다.
