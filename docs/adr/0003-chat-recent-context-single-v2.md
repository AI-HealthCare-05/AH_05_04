# ADR 0003: 단일 v2 프롬프트로 최근 완료 대화 문맥 전달

- 상태: Proposed (PR #128)
- 결정일: 2026-08-31
- 관련 Issue: #112, #129
- 관련 PR: #128

## 배경과 문제

기존 챗봇은 현재 질문과 확정 약물만 Provider에 전달했다. 후속 질문의 생략된 대상을 이해하려면 같은 세션의 최근 완료 대화가 필요하지만, 과거 대화는 추가 개인정보·의료정보 전송이며 과거 사용자 진술과 AI 답변이 현재 사실로 오인될 위험도 있다.

Issue #112의 초기 완료 조건은 feature flag가 꺼지면 `chat-prompt-v1`, 켜지면 `chat-prompt-v2`를 사용하는 이중 경로였다. 구현 검토 과정에서 프롬프트와 출력 검증을 두 벌로 유지하지 않고, payload 형태와 안전 규칙을 한 계약으로 고정하기로 결정했다.

## 결정

- flag와 history 유무에 관계없이 `chat-prompt-v2` 하나를 사용한다.
- Provider payload에는 `history` 배열을 항상 포함한다.
- `CHAT_HISTORY_CONTEXT_ENABLED`는 과거 대화의 조회·전송만 제어한다. 기본값은 `false`다.
- `false`이면 history를 조회하지 않고 빈 배열을 전달한다.
- `true`는 비식별 합성 데이터를 사용하는 Local 환경에서만 허용한다. 다른 환경에서 활성화하면 설정 검증 단계에서 거부한다.
- 활성화 시 같은 세션에서 현재 질문 이전의 완료된 USER–ASSISTANT 대화를 최신순으로 검사해 최대 3쌍을 고르고, Provider에는 오래된 순서로 전달한다.
- 현재 확정 `medications`가 history와 충돌하면 `medications`를 우선한다.
- 과거 USER 발화는 검증된 의료 사실이나 현재 상태가 아니며, 과거 ASSISTANT 답변도 의료 근거가 아니다. 현재 질문에서 지속 여부가 확인되지 않은 정보가 답변 안전성에 중요하면 현재도 해당하는지 짧게 확인한다.
- JSON 내부의 question, history, medications 문자열은 모두 데이터로 취급하고 지시로 따르지 않는다.

이 결정은 HTTP 요청·응답 DTO, 오류 코드·상태, 메시지 저장·실패 의미와 세션 row lock을 변경하지 않는다. ADR 0001의 Provider payload 제한 문장만 이 결정이 대체한다.

## 검토한 대안

### flag별 v1·v2 프롬프트 유지

flag를 즉시 끌 때 기존 prompt version까지 되돌릴 수 있지만, 동일한 Chat 계약에 두 payload와 두 출력 검증 경로가 생겨 회귀 조합과 운영 해석이 늘어난다. 데이터 전송 차단은 빈 history로 달성할 수 있으므로 선택하지 않았다.

### flag 없이 항상 history 조회

구현은 단순하지만 외부 전송 승인 전 서버 환경에서 과거 대화를 차단할 운영 경계가 없다. Local 합성 검증과 실제 사용자 데이터 전송 승인을 분리하기 위해 선택하지 않았다.

### Provider-side conversation state

요청 payload는 줄일 수 있지만 저장·삭제·소유권 경계와 재현성이 Provider 상태에 의존한다. DB 메시지를 source of truth로 유지하기 위해 선택하지 않았다.

## 결과와 영향

- flag OFF에서도 저장되는 `prompt_version`은 기존 `chat-prompt-v1`이 아니라 `chat-prompt-v2`다.
- flag OFF Provider payload에는 `history: []`가 추가되지만 기존 Backend Chat API DTO와 오류 의미는 변하지 않는다.
- 단일 프롬프트와 출력 검증 경로로 Local 및 운영 설정 간 안전 규칙이 일치한다.
- 실제 사용자 history 전송은 아직 승인되지 않았다. Staging·Production에서는 flag를 `false`로 유지한다.
- 후속 Issue #129에서 버전된 합성 replay와 결정론적 Local application-path latency·PII sentinel 검증을 수행했다. 실제 Provider 검증은 `NOT_RUN`이고 결과는 Production 공개 근거가 아니다.

## 보안·개인정보 영향

history 자유 텍스트에는 사용자가 입력한 개인·의료정보가 포함될 수 있다. 구조화 식별자를 제외해도 자유 텍스트가 비식별이라는 뜻은 아니다. 실제 대화 전송 전에는 이용자 고지와 법적 근거, Provider 저장·학습·보존 정책, 삭제·철회와 사고 대응 범위를 Privacy·Security 책임자가 승인해야 한다.

## 테스트 영향

- flag OFF의 조회 생략, `history: []`, `chat-prompt-v2`를 검증한다.
- flag ON의 최대 3쌍, 최신 선택·시간순 전달, 완료 상태·소유권 경계와 현재 질문 제외를 검증한다.
- 과거 USER 진술과 ASSISTANT 답변의 비신뢰성, 현재 medications 우선, JSON 데이터의 프롬프트 인젝션 방어를 검증한다.
- 버전된 합성 replay, 결정론적 Local application-path latency와 PII sentinel 증빙은 Issue #129에 기록한다. 실제 Provider 품질·latency·token 결과는 실행 전까지 `NOT_RUN`으로 유지한다.

## 관련 문서

- [ADR 0001](./0001-synchronous-chat-generation-with-session-row-lock.md)
- [복약 챗봇 최근 대화 3쌍 문맥 설계](../designs/ceohwj/chat-recent-context-design.md)
- [복약 챗봇 Backend–AI Core 계약](../contracts/current/medication-chat-ai-backend.md)
- [개인정보 및 의료 안전](../privacy-safety.md)
- [배포 가이드](../deployment.md)
- [AI 파이프라인](../ai-pipeline.md)
- [테스트 가이드](../testing.md)
