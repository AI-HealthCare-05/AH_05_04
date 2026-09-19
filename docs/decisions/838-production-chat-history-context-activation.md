# #838 Production Chat History Context Activation

- 상태: Proposed / Pending Approval
- 관련 Issue: #838
- 관련 PR: #834
- 구현 담당자: 정현우 (@ceohwj)
- 단일 담당 리뷰어: 송은영 (@phina-io, Track F Chat data boundary 소유자)
- 개인정보 정책 검토: 권가빈 (@hazelnutflavoured)

## 배경 및 맥락

기존 정본 계약(`medication-chat-ai-backend.md`, `deployment.md`, `api.md`, `privacy-safety.md`, `architecture.md`, `ai-pipeline.md`, `adr/0003`)은 비식별 합성 데이터를 사용하는 Local 환경에서만 `CHAT_HISTORY_CONTEXT_ENABLED=true`를 허용하고, Staging·Production에서는 `false` 강제 및 `history: []` 전송을 요구해 왔다.

PR #834는 Production 설정 검증에서 `CHAT_HISTORY_CONTEXT_ENABLED=true`를 허용하고, Compose를 통해 FastAPI 컨테이너에 환경 변수를 주입할 수 있는 런타임 readiness를 구현했다. 그러나 실제 사용자의 이전 대화가 외부 LLM(OpenAI) 입력으로 전송되는 것은 Track F Chat data-boundary 및 개인정보 처리 범위의 중요한 변경이므로, 정식 승인 evidence 없이 배포 게이트를 즉시 해제할 수 없다.

## 제안 결정 사항 (Proposed Semantics)

1. **기본값 Fail-closed**: `CHAT_HISTORY_CONTEXT_ENABLED`의 기본값은 `false`다.
2. **명시적 Opt-in**: 자동 활성화는 금지되며, 정식 승인 완료 후 Production 환경 변수에서 명시적으로 `true`로 설정할 때만 활성화된다.
3. **단일 컨테이너 주입**: 해당 플래그는 오직 `fastapi` 컨테이너에만 주입되며, `ai-worker`, `postgres`, `redis` 등 타 서비스에는 전달되지 않는다.
4. **동일 세션 격리 (Same-session context only)**: 현재 활성 `chat_session.id`와 일치하는 세션의 이전 완료 대화(`USER/NOT_APPLICABLE`과 `ASSISTANT/COMPLETED`)만 추출한다.
5. **교차 세션 혼입 금지 (Cross-session exclusion)**: 다른 세션의 대화는 동일 사용자의 것이라도 context 배열에 절대 포함되지 않는다.
6. **기존 문맥 알고리즘 보존**: 최신 30쌍 검사, 12,000자 예산 한도, 최대 3쌍 선택, 오래된 순 정렬(`question` 및 `answer`만 전달)의 기존 DTO 및 selection semantics는 그대로 유지한다.
7. **기존 구현 불변**: 프롬프트(`chat-prompt-v5`), ChatService 흐름, RAG 파이프라인, DB 스키마/마이그레이션, Protected Retrieval은 변경하지 않는다.

## 안전성 및 데이터 삭제 (Safety & Deletion)

- **탈퇴/삭제 연계**: 계정 탈퇴(`AccountDeletionRequestRepository`) 시 `chat_session` 및 `chat_message`는 `USER_DATA_DELETE_STATEMENTS`에 의해 완전 삭제되므로, 삭제된 대화가 과거 문맥으로 재생되거나 외부 LLM에 노출되는 위험은 없다.
- **Fail-closed 동작**: 플래그가 `false`이거나 비정상일 경우 이전 대화를 조회하지 않고 항상 빈 배열(`history: []`)을 전달한다.
- **롤백 절차 (Rollback)**: Production 환경에서 `CHAT_HISTORY_CONTEXT_ENABLED=false`로 변경 후 `fastapi` 컨테이너를 재생성(recreate)한다.

## 현재 발행/배포 조건 (Current Publication Condition)

- 현재 승인 상태는 **PENDING**이다.
- 송은영(Track F Chat data boundary 소유자)의 정식 승인 evidence가 등록되기 전까지 실제 Production 활성화는 차단된다.
- `scripts/deployment.sh`의 forced-false gate에서 `CHAT_HISTORY_CONTEXT_ENABLED`를 유지하여, 승인 전 `true` 배포 시도를 사전 차단한다.

## 활성화 승인 요건 (Activation Requirements)

실제 Production 활성화를 위해서는 다음 요건이 충족되어야 한다:
1. Track F Chat data boundary 소유자(송은영)의 명시적 승인
2. Privacy 정책 담당자(권가빈)의 외부 전송 범위 검토 및 게이트(`EXT-PRIV-003`) 승인
3. 자동화된 Same-session inclusion 및 Cross-session exclusion 회귀 테스트 통과
4. 활성화 배포 후 런타임 환경 변수(`true`) 및 FastAPI 컨테이너 health check 확인

---

## Phase B 전환 Delta (승인 후 실행 항목)

@phina-io 및 Privacy 승인 evidence가 확보되면 아래 작업을 단일 전환 PR/커밋으로 수행한다:
1. 본 Decision 상태를 `Accepted`로 전환
2. `docs/release-gates/post-mvp-1-external-approvals.md`의 `EXT-PRIV-003` 상태를 `Approved`로 전환 및 승인 링크 기록
3. 정본 계약(`docs/contracts/current/medication-chat-ai-backend.md` 등)의 서술을 "승인 완료에 따른 Production 명시적 opt-in 허용"으로 전환
4. `scripts/deployment.sh`의 forced-false gate 목록에서 `CHAT_HISTORY_CONTEXT_ENABLED` 제거
5. deployment contract test의 기대값을 "배포 허용"으로 전환
