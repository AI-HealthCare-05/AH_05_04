# ADR 0002: Post-MVP-1 비동기 AI 실행 기반

- 상태: Accepted target / Not implemented
- 기록일: 2026-08-24
- 관련 Issue: #68, #69
- 승인 원본: `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md`, `05_Architecture/System_Architecture_v2.md`

이 ADR은 승인 원본을 저장소 구현 문맥에 기록한 **승인된 목표 결정**이다. 아직 route·DTO·migration·Worker·테스트가 병합되지 않았으므로 현재 architecture를 설명하지 않으며 ADR 0001도 대체하지 않는다.

## 배경

현재 OCR·Guide·Chat은 FastAPI 요청 안에서 외부 Provider를 호출한다. 긴 실행이 HTTP·DB connection 생명주기에 묶이고, 재접속 복구·중복 전달·처방 version 변경·장애 복구를 하나의 공통 방식으로 다루기 어렵다.

## 목표 결정

1. OCR·Guide·Chat을 공통 `AI_JOB`의 6개 상태(`PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE`)로 처리한다.
2. 접수 transaction에서 Job, 도메인 placeholder, `OUTBOX_EVENT`, 멱등 레코드를 함께 commit한다. Outbox publisher가 Redis Stream에 at-least-once로 발행하고 reconciler가 commit 후 발행 전 중단을 복구한다.
3. Worker는 consumer group, lease와 fencing token으로 작업을 claim한다. 도메인 결과와 Job 상태를 DB에 commit한 뒤에만 ACK하며, 중복 전달에도 side effect는 한 번만 반영한다. poison 메시지는 quarantine commit 뒤 ACK한다.
4. Client는 REST polling으로 상태를 조회한다. `Retry-After`와 `retry_after_seconds`를 일치시키고 `COMPLETED`에서만 opaque `result_url`을 사용한다.
5. 결과는 불변 `prescription_version_id`에 귀속하고 active version과 다르면 `STALE`로 현재 노출을 차단한다.
6. `ASYNC_OCR`, `ASYNC_GUIDE`, `ASYNC_CHAT` 순으로 신규 접수만 canary 전환한다. rollback은 신규 접수를 이전 동기 경로로 돌리되 이미 접수된 비동기 Job은 drain한다.

at-least-once 전달의 중복 책임은 Backend의 멱등 접수, DB unique·조건부 갱신, Worker fencing과 결과 commit-before-ACK가 함께 진다. Redis는 전달 수단이며 Job·결과의 source of truth가 아니다.

## 검토한 대안

### 동기 처리 유지

현재 MVP에는 가장 단순하지만 장기 작업의 connection 점유, 재접속과 장애 복구 문제를 해결하지 못해 목표 구조로 선택하지 않았다. 실제 전환 전까지는 여전히 Current다.

### Transactional Outbox 없는 task queue

DB commit과 queue publish 사이의 중단으로 유실 또는 orphan 작업이 생길 수 있어 선택하지 않았다.

### SSE 결과 전달

추가 연결 수명주기와 reconnect 계약이 필요하다. v1은 REST polling으로 고정돼 있어 선택하지 않았다.

### Redis를 상태 source of truth로 사용

영속 도메인 결과, 소유권, 처방 version과 transaction 일관성을 보장하기 어려워 선택하지 않았다. MySQL이 권위 있는 상태를 유지한다.

## 영향과 검증

- API는 `202` 접수·상태 조회, 멱등 충돌, Chat session당 non-terminal Job 1개와 재접속 복구 계약을 구현해야 한다.
- migration, OpenAPI/DTO, Backend·Worker·Frontend 계약 및 통합 테스트를 함께 병합한다.
- 중복 publish/delivery, Worker 종료·lease reclaim, fencing 경쟁, poison quarantine, Redis 장애, STALE과 민감정보 비노출을 검증한다.
- 보존과 C·D·F 공개는 [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)를 별도로 충족한다.

## ADR 0001 전환 조건

Chat route·DTO·migration·Worker와 계약·통합·재접속 테스트가 실제 전환 PR에 함께 병합되고 `ASYNC_CHAT` 운영 전환 조건이 충족된 뒤에만 [ADR 0001](./0001-synchronous-chat-generation-with-session-row-lock.md)을 `Superseded`로 바꾼다. 문서 승인이나 이 ADR의 추가만으로는 상태를 변경하지 않는다.
