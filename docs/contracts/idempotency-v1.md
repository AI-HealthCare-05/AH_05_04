# 멱등성 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved target — 2026-08-24 팀 인계 기준 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md` |
| Last verified | 2026-08-24 |

## 적용 요청

비동기 Job을 생성하는 모든 POST 요청과 Post-MVP-1 B·C·D 동기 상태 변경 요청은 `Idempotency-Key` 헤더를 요구한다. 키는 16~255자의 ASCII 영숫자와 `-._:`만 허용하며 로그에는 원문을 남기지 않는다. 누락·빈 값은 `400 IDEMPOTENCY_KEY_REQUIRED`, 형식 오류는 `400 IDEMPOTENCY_KEY_INVALID`다.

## 식별 범위와 요청 해시

비동기 접수의 고유 범위는 `(user_id, OpenAPI operation_id, key_digest)`이다. `key_digest`는 원문 키를 서버 비밀키로 versioned HMAC-SHA-256 처리한 값이며 원문은 저장하지 않는다. 동기 상태 변경은 아래와 같이 `parent_resource_id`를 포함한 별도 `key_hmac` scope를 사용한다. Post-MVP-1은 인증 사용자가 직접 소유한 리소스에 수행하는 요청만 지원한다.

요청 지문은 다음 값을 canonical JSON으로 직렬화한 SHA-256이다.

- 비동기 요청의 `job_type`
- HTTP method와 정규화한 route template
- path의 도메인 식별자
- 의미 있는 query와 body
- 처방 기반 요청이면 `prescription_version_id`
- 파일을 사용하는 요청이면 필요한 file content digest

인증 토큰, trace ID, 전송 시각은 지문에서 제외한다.

## 비동기 Job 처리 규칙

| 상황 | 결과 |
|---|---|
| 새 키 | 도메인 placeholder, Job, Outbox와 함께 한 DB transaction에서 저장 |
| 같은 키·같은 지문 | 저장된 `job_id`로 Job을 조회해 새 Job 없이 최신 상태의 `202` 응답 반환 |
| 같은 키·다른 지문 | `409 IDEMPOTENCY_KEY_CONFLICT` |
| 최초 transaction rollback | 키도 저장하지 않아 안전하게 재시도 가능 |

동시 최초 요청은 DB unique constraint로 하나만 승리시킨 뒤, 패자는 저장된 요청 지문을 비교해 위 규칙을 적용한다.

비동기 Job 멱등 레코드는 응답 body snapshot을 저장하지 않는다. 동일 요청은 저장된 `job_id`로 현재 Job을 조회해 최신 `202`를 반환한다.

## 동기 상태 변경 처리 규칙

동기 B·C·D 쓰기의 고유 범위는 `(user_id, OpenAPI operation_id, parent_resource_id, idempotency_key_hmac)`이다. parent resource는 다음과 같다.

- B 일정: `prescription_version_medication_id`
- B Check-in·재알림: `occurrence_id`
- C Safety: `medication_checkin_id`; Barrier: `checkin_id`; ActionPlan 생성: `barrier_response_id`; ActionPlan 변경·follow-up: `support_action_plan_id`
- D OTC 평가: `prescription_version_id`이며 request hash에 선택 target 종류와 ID를 포함

권한·입력·revision·현재 상태 검사를 통과한 2xx mutation만 최초 성공 HTTP status와 canonical JSON body snapshot을 도메인 변경과 같은 transaction에서 저장한다. 4xx·5xx는 저장하지 않는다. 같은 키·같은 지문은 revision·현재 상태 검사보다 먼저 최초 snapshot을 그대로 재현하고, 같은 키·다른 지문은 `409 IDEMPOTENCY_KEY_CONFLICT`다.

snapshot은 암호화된 `MEDIUMBLOB`에 저장하되 application cap은 1MiB다. 의료 자유 텍스트와 Provider 원문은 넣지 않고 일반 로그에도 기록하지 않는다. 직렬화 결과가 cap을 넘으면 snapshot을 자르지 않으며 mutation 전에 `503 IDEMPOTENCY_RESPONSE_TOO_LARGE`와 alert로 실패한다.

## 보존

- 최초 접수 transaction이 성공한 시점부터 최소 24시간 유지하며 운영 설정 기본값은 7일이다.
- 만료 이후 같은 키는 새 요청으로 처리될 수 있으므로 사용자의 새 실행에는 항상 새 키를 발급한다.
- 감사·보안 정책이 더 긴 보존을 요구하면 더 긴 기간을 적용할 수 있다.

## 저장 필드

비동기 레코드는 `user_id`, `operation_id`, versioned `key_digest`, `request_hash`, `job_id`, `created_at`, `expires_at`을 저장하고 응답 snapshot은 저장하지 않는다.

동기 레코드는 `user_id`, `operation_id`, `parent_resource_id`, versioned `key_hmac`, `request_hash`, `response_status`, 암호화된 `response_body_snapshot`, `created_at`, `expires_at`을 저장한다. HMAC version의 물리 컬럼·인코딩과 키 교체 절차는 Privacy·보안 승인과 구현 PR에서 확정하며 이 계약에서 별도 저장 방식을 추정하지 않는다.
