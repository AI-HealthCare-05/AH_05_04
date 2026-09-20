# RAG 결과 연동 전 Guide·Chat Backend public projection 경계 (#180)

**문서 성격**: Guide Lane A public projection 구현 및 Chat 후속 경계 문서
**상태**: Proposed
**관련 이슈**: #180
**범위**: 현재 Sync `201 Created` Guide API의 release/citation persistence·public projection과 Chat 후속 경계 정리
**비범위**: #180 orchestration 구현, Citation Decision 구현, Citation Authorization 재판정, `202 Accepted` 전환, 새 API endpoint, Chat public DTO/persistence 확정

## 1. 목적

#180 RAG orchestration은 Guide·Chat의 최종 RAG 결과, Citation, Safety, Release 판단을 만들 예정이지만, 현재 Backend public API는 여전히 동기 `201 Created` 계약으로 동작한다.

이 문서는 #180 final output을 Backend가 어느 지점에서 받아 기존 Guide·Chat public response로 projection해야 하는지 고정한다. Guide는 canonical carrier를 저장·재조회하는 public 계약까지 구현됐고, Chat은 별도 Freeze 전까지 경계만 유지한다. 작업 목적은 RAG 구현과 Backend API 작업이 서로의 권위를 침범하지 않도록 하는 것이다.

Phase A 조사 이후 Guide Lane A의 canonical carrier가 확정되어 Guide persistence와 public DTO를 구현한다. Chat은 별도 Freeze 전까지 이 구현으로 완료됐다고 보지 않는다.

## 2. 관련 권위와 소비 순서

Backend Guide·Chat API는 #807의 raw Source Use Approval을 직접 소비하지 않는다.

아래는 현재 develop의 실행 순서가 아니라 #180 Phase B 이후 목표로 정렬할 target/pending sequence다. #857이 merge되기 전에는 2번 Runtime Bundle approval pin을 current/develop authority로 보지 않는다.

1. #807 `PATIENT_CITATION` Source Use Approval
2. Runtime Bundle approval pin (#857 pending/unmerged prerequisite)
3. Citation Source/Member Decision 및 Receipt
4. #180 orchestration final Authorized/Release result
5. Backend Guide·Chat public response projection

따라서 Backend Phase B 구현은 #180이 확정한 최종 public-safe 결과만 소비해야 한다. Source approval, Bundle pin, Citation member decision의 내부 판정을 Router나 public DTO에서 재판정하지 않는다.

관련 구현·계약 근거:

- #485는 Citation Authorization/Finalizer/Claim-Citation Validator의 pure 계층을 추가했지만 Graph, DB persistence, public DTO projection은 구현하지 않았다.
- #765는 Guide orchestration의 preflight → authoritative evidence handoff core를 연결했지만 Generator, Citation Authorization, Release Gate, fallback mapping, persistence, public API projection은 구현하지 않았다.
- #857은 현재 OPEN / unmerged 상태인 pending prerequisite이다. #857이 merge된 뒤에만 #807 Source Use Approval을 Runtime Bundle에 pinning하는 develop authority로 승격된다. request-time Citation Authorization, Source/Member public projection, Release Gate를 대체하지 않는다.
- 따라서 이 문서의 Backend 경계는 위 선행 작업을 current로 과장하거나 재판정하지 않고, #180 final Authorized/Release result를 소비할 projection 위치만 고정한다.

## 3. 현재 Guide Sync 201 흐름

확인 파일과 심볼:

- `backend/app/apis/v1/guide_routers.py`
  - `create_guide()`
  - `get_guide_detail()`
- `backend/app/services/guides.py`
  - `GuideService.create_guide()`
  - `GuideService.get_guide_detail()`
  - `_to_guide_data()`
- `backend/app/dtos/guides.py`
  - `GuideResponse`
  - `GuideData`
- `backend/app/models/guides.py`
  - `Guide`
  - `GuideCitation`
- `backend/app/repositories/guide_repository.py`
  - `GuideRepository.create()`
  - `GuideRepository.mark_completed()`
  - `GuideRepository.mark_failed()`
  - `GuideRepository.lock_if_current_version()`

현재 흐름:

1. `POST /api/v1/guides`가 `GuideService.create_guide()`를 호출한다.
2. 처방 소유권을 확인한다.
3. `ConsentPurpose.GUIDE` 동의 Gate를 통과해야 한다.
4. 활성 처방 version과 약품 목록을 확인한다.
5. `Guide` row를 `GENERATING`으로 생성한다.
6. `GuideGenerator.generate()`를 호출한다.
7. 저장 직전에 `lock_if_current_version()`으로 active version을 다시 확인한다.
8. `mark_completed()`가 `content`, `model_name`, `prompt_version`, `completed_at`, `generation_status=COMPLETED`를 저장한다.
9. `_to_guide_data()`가 `GuideResponse.data`를 만든다.

현재 public response 필드:

- `guide_id`
- `prescription_id`
- `prescription_version_id`
- `generation_status`
- `content`
- `model_name`
- `prompt_version`
- `requested_at`
- `completed_at`
- `release_decision` (`PASS | LIMITED | REJECTED | STALE | null`)
- `release_is_current` (`boolean | null`)
- `fallback_code`, `fallback_text` (nullable)
- `citations[]` (`source_type`, `source_code`, `source_version`, `locator`, `display_order`)

`GET /api/v1/guides/{guide_id}`도 같은 `_to_guide_data()`를 사용한다. 따라서 Guide의 public projection seam은 `_to_guide_data()`와 `GuideRepository.mark_completed()` 주변이다.

## 4. 현재 Chat Sync 201 흐름

확인 파일과 심볼:

- `backend/app/apis/v1/chat_routers.py`
  - `send_chat_message()`
  - `list_chat_messages()`
- `backend/app/services/chat.py`
  - `ChatService.send_message()`
  - `ChatService.list_messages()`
  - `_to_message_data()`
- `backend/app/services/chat_generator_engine.py`
  - `ChatGeneratorEngine.reply()`
- `backend/app/dtos/chat.py`
  - `SendChatMessageResponse`
  - `SendChatMessageData`
  - `ChatMessageListResponse`
  - `ChatMessageData`
- `backend/app/models/chat.py`
  - `ChatSession`
  - `ChatMessage`
  - `ChatCitation`
- `backend/app/repositories/chat_repository.py`
  - `ChatRepository.create_message()`
  - `ChatRepository.mark_completed()`
  - `ChatRepository.commit_failed_message_pair()`
  - `ChatRepository.list_messages()`
  - `ChatRepository.lock_if_current_version()`

현재 `POST /api/v1/chat-sessions/{session_id}/messages` 흐름:

1. `ChatService.send_message()`가 session 소유권을 확인한다.
2. session이 `ACTIVE`인지 확인한다.
3. session의 `prescription_version_id`가 active version과 일치하는지 확인한다.
4. `ConsentPurpose.CHAT` 동의 Gate를 통과해야 한다.
5. 현재 처방 version의 medications를 조회한다.
6. history context가 켜져 있으면 최근 완료된 대화 pair 최대 3개를 선택한다.
7. USER message와 ASSISTANT `PENDING` message를 생성한다.
8. ASSISTANT message를 `GENERATING`으로 바꾼다.
9. `self._engine.reply(chat_input)`을 호출한다.
10. 저장 직전에 `_reject_stale_generation()`으로 active version을 다시 확인한다.
11. `mark_completed()`가 ASSISTANT message의 `content`, `model_name`, `prompt_version`, `completed_at`, `generation_status=COMPLETED`를 저장한다.
12. `SendChatMessageData`를 반환한다.

현재 send response 필드:

- `user_message_id`
- `assistant_message_id`
- `session_id`
- `generation_status`
- `content`
- `model_name`
- `prompt_version`
- `created_at`
- `completed_at`

현재 `GET /api/v1/chat-sessions/{session_id}/messages` 흐름:

1. session 소유권을 확인한다.
2. session version current 여부를 확인한다.
3. `ChatRepository.list_messages()`로 메시지를 조회한다.
4. `_to_message_data()`가 `ChatMessageData`를 만든다.

현재 list response의 message 필드:

- `message_id`
- `role`
- `content`
- `generation_status`
- `created_at`

Chat은 send response와 rediscovery/list response의 public shape가 다르다. #180 결과를 Chat public response에 포함하려면 send response만 확장할지, list rediscovery 응답도 함께 확장할지 contract Freeze 때 결정해야 한다.

## 5. 현재 Sync 201 projection candidate seam

Router에 RAG logic을 직접 넣지 않는다.

이 절은 현재 Sync `201 Created` API에서 public response를 만드는 현행 projection 위치를 정리한다. #180의 최종 async execution consumer authority 또는 `JOB_EXECUTE` runtime seam을 여기로 고정한다는 뜻이 아니다. Final async consumer seam은 #180 orchestration과 `202 Accepted` contract가 Freeze된 뒤 별도로 확정한다.

Guide의 현행 projection candidate seam:

- `GuideService.create_guide()` 내부에서 현재 `GuideGenerator.generate()` 결과를 받는 경계
- 저장 경계는 `GuideRepository.mark_completed()`
- public projection 경계는 `_to_guide_data()`

Chat의 현행 projection candidate seam:

- `ChatService.send_message()` 내부에서 현재 `self._engine.reply(chat_input)` 결과를 받는 경계
- 저장 경계는 `ChatRepository.mark_completed()`
- send public projection 경계는 `SendChatMessageData(...)`
- rediscovery public projection 경계는 `_to_message_data()`

`backend/app/services/guide_intake.py`의 `GuideJobIntakeTransactionAdapter`는 후속 `202 Accepted + JobStatusResponse` 전환을 위한 adapter다. 현재 `/guides` production router는 Sync `201 Created` 흐름을 유지하므로 이 Phase A에서는 연결하지 않는다.

현재 Chat에는 Guide와 같은 `chat_intake.py` 202 adapter가 없다. 따라서 Chat도 현재 Sync `201 Created` 흐름 기준으로만 경계를 정리한다.

## 6. 현재 저장·응답 가능 필드와 gap

현재 Guide·Chat 공통으로 저장 가능한 값:

- public answer `content`
- `model_name`
- `prompt_version`
- `generation_status`
- `completed_at`

현재 모델에는 citation 테이블이 존재한다.

- `GuideCitation`
- `ChatCitation`

legacy completion 저장 경로는 citation을 함께 쓰지 않는다. canonical carrier용 repository 경계는 release result, approved answer/fallback과 ordered verified citation을 저장하고 GET에서 같은 결과를 복원한다. 실제 runtime callable은 아직 이 경계에 연결되지 않았다.

- Chat의 `PASS | LIMITED | REJECTED | STALE` public projection
- Chat의 fallback 및 public-safe citation DTO
- source title/URL/excerpt 규칙
- score/rank/confidence
- release receipt/provenance
- malformed/unauthorized citation 제거 또는 차단 결과
- RAG runtime bundle/execution manifest provenance

`model_name`과 `prompt_version`은 현재 Provider generation provenance로 사용된다. Runtime Bundle, Citation Authorization, Release Gate provenance를 이 두 필드에 섞지 않는다.

## 7. 남은 blocked contract fields

아래 항목은 후속 contract Freeze 전까지 임의 생성하지 않는다.

- Chat Citation DTO/schema
- Chat fallback/release DTO
- source URL/title/excerpt 규칙
- score/rank/confidence
- 새 API endpoint
- `202 Accepted` 전환 응답
- `job_id`/`status_url`/`result_url` 연결
- polling 계약
- Frontend display rule

후속 Phase B는 #180 final output이 확정된 뒤 위 항목 중 필요한 것만 실제 DTO/API/DB/test로 반영한다.

## 8. Gate와 실행 순서 유지 조건

#180 결과 소비가 추가되더라도 현재 Backend Gate 순서는 유지한다.

Guide:

1. Prescription ownership
2. `GUIDE` consent
3. active version 및 medication 검증
4. RAG/Generator 호출
5. 저장 직전 current version 재확인
6. persistence
7. public response projection

Chat:

1. Session ownership
2. session active 검증
3. prescription version current 검증
4. `CHAT` consent
5. medication/history context 구성
6. USER/ASSISTANT message 생성
7. RAG/Engine 호출
8. 저장 직전 current version 재확인
9. persistence
10. public response projection

ownership, current version, consent 실패 시 Provider/LLM/RAG 호출은 없어야 한다. #180 결과도 이 경계를 우회하지 않는다.

## 9. Phase B가 받을 최소 output 후보

Guide public v1은 `release_decision`, `release_is_current`, nullable fallback과 ordered public-safe citation으로 확정됐다. 아래 후보 중 Guide public v1에 포함되지 않은 값과 Chat 값은 별도 Freeze 전까지 schema로 확정하지 않는다.

- public answer text 또는 no-public-answer/fallback content
- final release decision
- public-safe citation list
- fallback/limited/rejected reason code
- generation model/prompt metadata
- internal provenance reference
- stale/context mismatch indicator
- unauthorized/malformed citation 처리 결과

Guide는 내부 answer·citation 좌표와 public DTO 필드를 구분해 저장한다. Chat 후속 contract Freeze에서는 위 값이 public DTO, 내부 persistence, 즉시 projection 중 어디에 속하는지 별도로 결정해야 한다.

## 10. Backend projection helper boundary

#906이 제공하는 `rag_runtime.guide_release_projection.GuideRuntimeReleaseProjectionOutcome`을 소비하는
Backend `project_guide_runtime_release()` helper를 둔다.
이 helper는 service/application 계층에서 사용할 내부 projection candidate를 만들고, repository가 그 candidate를 Guide release/citation persistence에 원자적으로 저장한다. public DTO 직렬화는 `_to_guide_data()`가 담당한다.
Backend는 `GuideRuntimeReleaseResult` 또는 다른 `ai_worker` object를 직접 받지 않으며, Worker 내부 구조를
`getattr()` 등으로 해석하지 않는다. #180 canonical adapter가 만든 shared carrier 또는 typed unavailable만 소비한다.

helper가 하는 일:

- `PASS`의 `GuideRuntimeApprovedAnswer`와 ordered `GuideRuntimeVerifiedCitation`을 손실 없이 내부 후보로 보존한다.
- `LIMITED`, `REJECTED`, `STALE`의 `GuideRuntimeApprovedFallback` code/text만 내부 후보로 보존한다.
- `GuideRuntimeReleaseProjectionUnavailable`은 answer, fallback, citation이 없는 fail-closed 후보로 변환한다.
- ordered verified citation의 내부 좌표를 보존해 persistence 경계로 전달한다.

helper가 하지 않는 일:

- Card, `AuthorizedCitationSelection`, receipt 또는 Worker 내부 object 해석
- citation authorization·exact binding 재판정, identity key 축약·재구성, citation deduplicate·reorder
- approved answer 재조합 또는 fallback code/text 정규화
- `GuideService.create_guide()`의 runtime callable wiring
- public DTO 직렬화 또는 DB row 직접 작성
- source URL/title/excerpt 추가 생성
- 내부 execution/evidence/reason/authorization receipt/score/rank/confidence/raw source 노출

answer와 citation은 하나의 shared PASS carrier에서만 오며, Backend가 answer를 얻기 위해 Card를 별도로 읽는 경로를
두지 않는다. citation의 Card/authorization 결속은 canonical Worker adapter 이전 단계에서 완료되며 Backend helper는
그 결정을 반복하지 않는다.

`GuideCitation`은 legacy citation과 runtime citation을 서로 배타적인 shape로 저장한다. Runtime row는 `source_snapshot_id`, `source_snapshot_member_id`, `source_code`, `source_version`, `locator`, `content_sha256` 및 내부 exact-binding 좌표를 보존한다. public 응답은 `source_type`, `source_code`, `source_version`, `locator`, `display_order`만 노출한다. Guide release row와 ordered citation은 같은 저장 경계에서 기록되며 POST와 GET/rediscovery가 같은 Citation/Fallback 의미를 복원한다.

## 11. target test locations

Guide public projection은 다음 위치에서 계약과 회귀를 검증한다. Chat은 contract Freeze 후 아래 Chat 위치에 테스트를 추가한다.

Guide service seam:

- `backend/app/tests/guide_ai/test_backend_contract.py`
- `backend/app/tests/services/test_guides.py`

Guide API/public serialization:

- 현재 직접적인 Guide API serialization 테스트가 부족하면 Phase B에서 `backend/app/tests/guide_apis/` 아래 전용 API 테스트를 추가한다.
- 기존 live/smoke 성격 검증은 `backend/app/tests/release_validation/test_ai_one_cycle_smoke.py`의 `/api/v1/guides` 경로를 대조한다.
- service-only 계약은 `backend/app/tests/guide_ai/test_backend_contract.py`와 `backend/app/tests/services/test_guides.py`에 둔다.

Chat service seam:

- `backend/app/tests/chat/test_chat_service.py`

Chat API/public serialization:

- `backend/app/tests/chat_apis/test_chat_message_api.py`

Chat rediscovery/list regression:

- `backend/app/tests/chat_apis/test_chat_message_api.py`
- `backend/app/tests/repositories/test_chat_repository.py`

Chat concurrency/stale regression:

- `backend/app/tests/chat_integration/test_chat_concurrency.py`

최소 케이스:

- normal answer approved citation
- limited/fallback answer
- rejected/no-public-answer
- stale/current version mismatch
- malformed citation
- unauthorized citation
- unauthorized/malformed citation이 public response와 persisted public projection에 남지 않는 경우

## 12. 완료 기준

현재 Guide Lane A 구현이 완료됐다는 것은 다음을 의미한다.

- Guide Sync `201 Created`와 GET/rediscovery 계약을 유지한다.
- canonical Guide release carrier를 Backend service seam에서만 소비한다.
- Guide release/fallback과 ordered citation을 저장하고 같은 public 결과로 복원한다.
- legacy Guide row는 nullable release fields와 빈 citation으로 호환한다.
- Guide Frontend는 public 필드만 소비하고 malformed 조합을 fail-closed한다.
- Chat의 미확정 DTO/persistence와 runtime callable wiring을 후속 범위로 분리한다.

현재 완료 상태는 다음을 의미하지 않는다.

- #180 orchestration 구현 완료
- RAG production 연결 완료
- Citation Authorization 구현 완료
- `202 Accepted` 전환
- Chat public citation/fallback DTO 또는 Frontend 반영
- Production 공개 승인
