# #838 Production Chat History Context Activation

- 상태: Accepted
- 승인 완료일: 2026-09-20 (KST)
- 관련 Issue: #838
- 관련 PR: #834
- 구현 담당자: 정현우 (@ceohwj)
- 단일 담당 리뷰어: 송은영 (@phina-io, Track F Chat data boundary 소유자)
- 개인정보 정책 검토: 권가빈 (@hazelnutflavoured)

## 승인 Evidence

- Privacy: @hazelnutflavoured, comment `5743158534`, 2026-09-19T15:39:59Z
  https://github.com/AI-HealthCare-05/AH_05_04/issues/838#issuecomment-5743158534
- AI/RAG: @ceohwj, comment `5743370061`, 2026-09-19T16:11:10Z
  https://github.com/AI-HealthCare-05/AH_05_04/issues/838#issuecomment-5743370061
- Track F Chat data boundary: @phina-io, comment `5746235826`, 2026-09-19T23:54:37Z
  https://github.com/AI-HealthCare-05/AH_05_04/issues/838#issuecomment-5746235826

## 배경 및 맥락

기존 정본 계약(`medication-chat-ai-backend.md`, `deployment.md`, `api.md`, `privacy-safety.md`, `architecture.md`, `ai-pipeline.md`, `adr/0003`)은 비식별 합성 데이터를 사용하는 Local 환경에서만 `CHAT_HISTORY_CONTEXT_ENABLED=true`를 허용하고, Staging·Production에서는 `false` 강제 및 `history: []` 전송을 요구해 왔다.

PR #834는 Production 설정 검증에서 `CHAT_HISTORY_CONTEXT_ENABLED=true`를 허용하고, Compose를 통해 FastAPI 컨테이너에 환경 변수를 주입할 수 있는 런타임 readiness를 구현했다. 그러나 실제 사용자의 이전 대화가 외부 LLM(OpenAI) 입력으로 전송되는 것은 Track F Chat data-boundary 및 개인정보 처리 범위의 중요한 변경이므로, 정식 승인 evidence 없이 배포 게이트를 즉시 해제할 수 없다.

## 결정 사항

1. **기본값 Fail-closed**: `CHAT_HISTORY_CONTEXT_ENABLED`의 기본값은 `false`다.
2. **명시적 Opt-in**: 자동 활성화는 금지되며, 정식 승인 완료 후 Production 환경 변수에서 명시적으로 `true`로 설정할 때만 활성화된다.
3. **단일 컨테이너 주입**: 해당 플래그는 오직 `fastapi` 컨테이너에만 주입되며, `ai-worker`, `postgres`, `redis` 등 타 서비스에는 전달되지 않는다.
4. **동일 세션 격리 (Same-session context only)**: 현재 활성 `chat_session.id`와 일치하는 세션의 이전 완료 대화(`USER/NOT_APPLICABLE`과 `ASSISTANT/COMPLETED`)만 추출한다.
5. **교차 세션 혼입 금지 (Cross-session exclusion)**: 다른 세션의 대화는 동일 사용자의 것이라도 context 배열에 절대 포함되지 않는다.
6. **기존 문맥 알고리즘 보존**: 최신 30쌍 검사, 12,000자 예산 한도, 최대 3쌍 선택, 오래된 순 정렬(`question` 및 `answer`만 전달)의 기존 DTO 및 selection semantics는 그대로 유지한다.
7. **기존 구현 불변**: 프롬프트(`chat-prompt-v6`), ChatService 흐름, RAG 파이프라인, DB 스키마/마이그레이션, Protected Retrieval은 변경하지 않는다.

## 안전성 및 데이터 삭제 (Safety & Deletion)

- **탈퇴/삭제 연계**: 계정 탈퇴(`AccountDeletionRequestRepository`) 시 `chat_session` 및 `chat_message`는 `USER_DATA_DELETE_STATEMENTS`에 의해 완전 삭제되므로, 삭제된 대화가 과거 문맥으로 재생되거나 외부 LLM에 노출되는 위험은 없다.
- **Fail-closed 동작**: 플래그가 `false`이거나 비정상일 경우 이전 대화를 조회하지 않고 항상 빈 배열(`history: []`)을 전달한다.
- **롤백 절차 (Rollback)**: Production 환경에서 `CHAT_HISTORY_CONTEXT_ENABLED=false`로 변경 후 `fastapi` 컨테이너를 재생성(recreate)한다.

## 현재 발행/배포 조건 (Current Publication Condition)

- 필요한 Privacy, AI/RAG 및 Track F Chat data boundary 승인 evidence가 모두 등록되었다.
- Production은 기본값 `false`를 유지하며, 명시적으로 `true`를 설정한 경우에만 history context를 활성화할 수 있다.
- 이 결정은 Production 활성화를 자동 수행하지 않는다. 실제 활성화는 별도 배포 승인과 activation smoke를 거쳐야 한다.

## 활성화 승인 요건 (Activation Requirements)

실제 Production 활성화를 위해서는 다음 요건이 충족되어야 한다:
1. Track F Chat data boundary 소유자(송은영)의 명시적 승인 — 완료
2. Privacy 정책 담당자(권가빈)의 외부 전송 범위 검토 및 게이트(`EXT-PRIV-003`) 승인 — 완료
3. 자동화된 Same-session inclusion 및 Cross-session exclusion 회귀 테스트 통과 — 완료
4. 활성화 배포 후 런타임 환경 변수(`true`) 및 FastAPI 컨테이너 health check 확인 — 실제 활성화 시 수행

---

## Phase B Publication Delta

승인 evidence에 따라 다음 publication contract를 적용한다:
1. 본 Decision은 `Accepted`다.
2. `EXT-PRIV-003`은 `Approved`다.
3. 정본 계약은 Production 명시적 opt-in을 허용한다.
4. `scripts/deployment.sh`의 forced-false gate는 Chat History를 제외하되 다른 공개 gate를 유지한다.
5. deployment contract test는 Chat History flag 자체가 배포를 차단하지 않음을 검증한다.
