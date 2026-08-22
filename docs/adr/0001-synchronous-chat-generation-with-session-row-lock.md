# ADR 0001: 세션 row lock을 사용하는 동기식 챗봇 생성

- 상태: Accepted
- 결정일: 2026-08-21
- 관련 Issue: #38
- 관련 PR: 아직 생성하지 않음 (`feat/38-chat-ai-backend-integration`)

## 배경과 문제

복약 챗봇 Backend는 한 HTTP 요청 안에서 사용자 질문을 저장하고 외부 AI를 호출한 뒤 ASSISTANT 메시지를 완료 또는 실패 상태로 저장한다. 같은 세션에서 여러 요청이 동시에 마지막 `message_seq`를 읽으면 동일한 번호를 선택해 메시지 순서가 충돌할 수 있다.

질문을 저장한 뒤 잠금을 해제하고 AI를 호출하면 DB connection 점유 시간은 줄어들지만, 요청 완료 순서와 메시지 순서를 일치시키려면 별도의 예약 상태, 재시도, timeout 복구와 작업 조정자가 필요하다. 현재 시스템에는 세션별 작업 queue나 비동기 결과 전달 계약이 없다.

오류가 발생한 경우에는 원본 Provider 본문이나 예외 chain을 노출하지 않으면서 USER 메시지와 FAILED ASSISTANT 메시지를 한 쌍으로 보존해야 한다.

## 결정

복약 챗봇 생성은 기존 동기 HTTP 계약을 유지하며 다음 transaction 경계를 사용한다.

1. 소유권 조건을 포함한 `SELECT ... FOR UPDATE`로 대상 `CHAT_SESSION` 한 행을 잠근다.
2. 잠금을 획득한 뒤 최신 `message_seq`를 조회하고 USER와 PENDING ASSISTANT 메시지를 생성한다.
3. 같은 transaction과 DB connection을 유지한 상태에서 외부 AI를 호출한다.
4. 성공하면 ASSISTANT를 COMPLETED로 변경하고 request transaction 전체를 commit한다.
5. AI 생성이 실패하면 원본 오류의 `except` 범위를 벗어난 뒤 안전한 고정 metadata로 ASSISTANT를 FAILED로 변경하고 USER·ASSISTANT 쌍을 명시적으로 commit한다.
6. 같은 세션의 후속 요청은 앞 요청이 commit 또는 rollback할 때까지 기다린다. 서로 다른 세션은 서로 다른 session row를 잠그므로 row-lock 수준에서는 독립적으로 처리한다.

Provider-neutral Adapter 경계는 유지한다. Backend는 현재 질문과 확정 처방의 약물만 Adapter에 전달하고 사용자·세션·처방·메시지 식별자와 과거 대화는 Provider payload에서 제외한다.

이번 결정에서는 세션별 queue, 비동기 worker, `NOWAIT`, 새로운 409 응답, admission control과 rate limiting을 도입하지 않는다.

## 검토한 대안

### AI 호출 전에 transaction과 row lock 해제

DB connection 점유 시간은 줄어든다. 하지만 여러 요청의 메시지 번호와 완료 순서를 안전하게 조정하려면 sequence 예약, idempotency, 중단된 GENERATING 메시지 복구와 재시도 정책이 추가로 필요하다. 현재 동기 API 계약 안에서 이를 부분적으로 도입하면 실패 상태와 사용자 관찰 순서가 불명확해지므로 선택하지 않았다.

### 세션별 queue와 비동기 worker

긴 외부 호출을 request DB transaction에서 분리하고 backpressure를 제공할 수 있어 장기적으로 가장 확장성 있는 대안이다. 그러나 작업 queue, 상태 조회 또는 결과 알림, 재시도·중복 실행 방지와 새 운영 인프라가 필요하다. Issue #38의 기존 API body와 dependency를 유지한다는 범위를 넘으므로 후속 설계 대상으로 남긴다.

### `NOWAIT` 또는 lock 실패 전용 응답

긴 lock 대기를 빠르게 거부할 수 있다. 하지만 새로운 충돌 상태와 HTTP 오류 계약을 Frontend와 합의해야 하고 사용자의 재시도 동작도 정의해야 한다. 이번 변경에서는 기존 공통 오류 계약을 유지하기 위해 선택하지 않았다.

### admission control 또는 rate limiting

DB pool 고갈 가능성을 낮출 수 있다. 실제 환경의 worker 수, pool·overflow와 트래픽 수치가 없는 상태에서 제한값을 정하면 근거 없는 운영 정책이 된다. 배포 환경 측정 결과가 현재 수용량을 충족하지 못할 때 별도 계약과 Issue로 도입한다.

## 결과와 영향

### 긍정적 영향

- 동일 세션의 USER·ASSISTANT 메시지 번호가 충돌하지 않는다.
- 성공과 실패 모두 단일 session row lock 아래에서 결정적인 순서로 저장된다.
- 요청이 중단되면 transaction rollback으로 불완전한 GENERATING 메시지가 영구 저장되지 않는다.
- 실패 persistence를 Provider 오류 처리 범위 밖에서 수행해 원본 AI 예외 chain 노출을 방지한다.
- 기존 동기 API response body와 Frontend 계약을 변경하지 않는다.

### 비용과 위험

- 외부 AI 호출 동안 DB transaction과 connection을 점유한다.
- 같은 세션의 요청은 앞 요청 수와 AI 생성 시간에 비례해 대기하며, 세 개 이상 동시 요청에는 유한한 end-to-end 최대시간을 보장하지 않는다.
- row lock waiter도 DB connection을 사용하므로 전체 in-flight chat 요청이 pool 수용량에 가까워지면 비채팅 요청도 connection 대기를 겪을 수 있다.
- reverse proxy timeout이나 MySQL lock wait timeout이 애플리케이션의 AI timeout보다 짧으면 응답 또는 잠금 획득 전에 요청이 실패할 수 있다.

## 보안·개인정보 영향

- 질문과 약물은 승인된 메시지·처방 저장 계약과 Provider 입력에만 사용한다.
- Provider payload에는 현재 질문과 허용된 약물 필드만 포함한다.
- Provider 본문과 원본 예외 chain은 DB metadata, HTTP 오류와 일반 로그에 저장하거나 노출하지 않는다.
- SQLAlchemy bind parameter는 `hide_parameters=True`로 예외와 echo 로그에서 숨긴다.
- 테스트와 문서 예시는 비식별 합성 데이터만 사용한다.

## 운영 영향과 배포 Gate

이 결정을 구현·merge하는 것과 Production 배포 승인은 분리한다. 실제 배포 환경이 생기기 전에는 timeout, connection 수용량과 승인자를 추정하지 않는다.

Production 배포 전 다음 내용을 실제 환경 기준으로 `docs/deployment.md`에 기록하고 승인해야 한다.

- OpenAI 전체 timeout을 `T`, 처리 여유를 `M`으로 두었을 때 Nginx `proxy_read_timeout >= 2 × T + M`
- MySQL `innodb_lock_wait_timeout > T + M`
- AI 호출 중인 요청과 row lock waiter를 포함한 worker별 전체 in-flight chat 수
- DB pool size, overflow, pool wait 정책과 비채팅 요청용 예비 connection
- 외부 AI 호출 동안 transaction과 connection을 유지하는 tradeoff의 운영 승인자

위 조건을 충족하지 못하면 row lock을 완화하지 않고 admission control, rate limiting 또는 비동기 worker 전환을 별도 ADR과 공유 계약으로 설계한다.

## 테스트 영향

- 실제 MySQL 8에서 동일 세션의 두 개·세 개 동시 요청이 충돌 없이 직렬화되는지 검증한다.
- 서로 다른 세션은 row-lock 수준에서 병렬 진입하는지 검증한다.
- AI 실패 후 USER·FAILED ASSISTANT 쌍이 후속 request rollback에도 보존되는지 검증한다.
- 잠금 획득 전 lock wait timeout은 새 메시지를 만들지 않고 기존 공통 500 계약을 사용하는지 검증한다.
- SQL 예외와 echo 로그에 합성 bind parameter가 노출되지 않는지 검증한다.

## 관련 문서

- [복약 챗봇 AI Backend 연동 설계](../designs/ceohwj/medication-chat-ai-backend-integration-design.md)
- [복약 챗봇 Backend–AI Core 계약](../contracts/medication-chat-ai-backend.md)
- [API 명세](../api.md)
- [배포 가이드](../deployment.md)
