# 개인정보 및 의료 안전

## 책임과 승인

- 권가빈: 개인정보·민감정보 수집·이용, 외부 Provider 처리 고지, 보존·삭제 정책과 Privacy 공개 승인 인수
- 송은영: 인증·소유권·암호화·키 관리·로그 마스킹·TTL·사용자 삭제·legal hold·감사 통제 구현과 증빙
- 남한솔: 회원가입·처방 업로드·Guide·Chat의 동의·철회·삭제 요청 UX와 접근성
- 김지혜: OCR·Worker 경계의 최소 필드 전송, 민감정보 비로그와 합성 fixture 증빙
- 정현우: AI·RAG·Provider·평가 경계의 최소 필드 전송, 민감정보 비로그와 합성 fixture 증빙 및 세부 배정 팀원 결과 취합

Security·Privacy는 별도 기능 Track이 아니라 모든 PR의 공통 완료 조건입니다. PR에는 구현 담당자와 담당 리뷰어를 별도로 지정하며, 코드 리뷰는 외부 Privacy·의료·약학·Source 공개 승인을 대신하지 않습니다.

## 데이터 분류

| 구분 | 저장소 포함 | 원칙 |
|---|---:|---|
| 실제 환자·처방·진료 데이터 | 금지 | 승인된 외부 저장소에서 접근 통제 |
| 비식별 합성 샘플 | 허용 | 재식별 가능성과 출처 확인 |
| OCR 원문·미검토 값 | 운영 데이터 | 사용자 승인 전 확정 입력 금지 |
| 확정 처방 상태 | 운영 데이터 | 버전과 승인 주체·시각 추적 |
| AI 모델·프롬프트 정보 | 운영 데이터 | 현재 MVP 생성 결과와 함께 버전 추적 |
| AI 근거·검증 결과 | Post-MVP 운영 데이터 | RAG·Citation·Safety 구현 후 Source·Rule·검증·Runtime Bundle version 추적 |

## 필수 안전 규칙

1. OCR 원문값, 정규화값, 사용자 수정값과 확정값을 구분합니다.
2. AI 입력에는 목적 달성에 필요한 최소 정보만 포함합니다.
3. 처방과 생성 결과가 불일치하면 사용자에게 게시하지 않습니다.
4. 근거가 없거나 상충하면 답변 범위를 제한합니다.
5. 약 중단, 용량과 복용 시간 변경을 AI가 임의로 권고하지 않습니다.
6. 응급·고위험 신호는 상담 또는 응급 도움 안내를 우선합니다.
7. 의료문서·대화·AI 로그의 보존·삭제 기준을 데이터 종류별로 기록합니다.

## Guide AI v3 외부 전송과 공개 경계

- Guide Provider payload는 0-based `source_index`와 `guidance_intent`만 포함합니다.
- `guidance_intent`는 `timing_text` 존재 여부에서 파생된 의료 metadata이며 외부 전송 승인 범위에 포함합니다.
- 약명·제품 함량·용량·단위·횟수·복용 시점·기간, 사용자·문서·처방·약물 식별자, OCR 원문과 내부 오류 metadata는 Guide Provider에 전달하지 않습니다.
- LLM 출력은 입력과 같은 index·intent 및 코드에 고정된 intent별 guidance·공통 notice 집합에 NFC 정규화와 trim 후 정확히 일치해야 합니다.
- 승인 집합 밖 문장, 새 처방 사실·복용법, 숫자·의료 주장·처방 변경·마크업 위반은 `GuideGenerationSafetyError`로 전체 공개를 차단합니다.
- 오류 저장·API 응답에는 고정 문구만 사용하고, 일반 생성 실패 로그에는 오류 분류명과 안전한 rule ID만 기록합니다. 처방값이나 생성 본문을 예외 chain에 남기지 않습니다.
- 최종 가이드의 약명·용량·횟수·시점·기간과 불완전 용량 확인은 기존 Backend renderer만 원본 확정 입력에서 표시합니다.

`guide-prompt-v3`의 제한 생성과 `guide-v3-eval-v1` Local 합성 검증은 외부 Provider 정책 승인이나 Production 공개 승인을 대체하지 않습니다.

## Chat AI v2 최근 대화 외부 전송과 공개 경계

- Chat Provider payload는 현재 질문, 현재 확정 약물의 허용 필드와 `history` 배열만 포함합니다.
- `CHAT_HISTORY_CONTEXT_ENABLED`의 기본값은 `false`이며, 이때 이전 대화를 조회하지 않고 `history: []`를 전달합니다.
- flag 활성화는 비식별 합성 데이터를 사용하는 Local 검증에서만 허용합니다. Staging·Production을 포함한 다른 환경에서는 설정 검증이 활성화를 거부합니다.
- history는 같은 세션에서 현재 질문 이전에 완료된 USER–ASSISTANT 대화 최대 3쌍만 포함합니다. 사용자·세션·처방·문서·메시지 식별자, 상태, 시각과 오류 metadata는 포함하지 않습니다.
- 구조화 식별자를 제외해도 history 자유 텍스트에는 사용자가 입력한 개인·의료정보가 있을 수 있으므로 비식별이라고 간주하지 않습니다.
- 과거 USER 발화는 검증된 의료 사실이나 현재 상태가 아니며, 과거 ASSISTANT 답변도 근거가 아닙니다. 현재 확정 medications를 우선하고 안전상 중요한 과거 정보는 현재도 해당하는지 확인합니다.
- JSON 내부 문자열은 지시가 아닌 데이터로 취급하며, history의 시스템 규칙 변경·역할 변경·프롬프트 공개 요청을 따르지 않습니다.

실제 사용자 대화를 전송하려면 이용자 고지와 적용 가능한 법적 근거, Provider 저장·학습·보존 정책, 삭제·철회와 사고 대응 범위를 Privacy·Security 책임자가 승인해야 합니다. `chat-prompt-v2`와 Local 결정론적 테스트는 이 승인을 대신하지 않습니다. 버전된 합성 평가, latency와 PII sentinel 검증은 [Issue #129](https://github.com/AI-HealthCare-05/AH_05_04/issues/129)의 `NOT_RUN` 후속 작업이며 완료 전까지 Production 공개 근거로 사용할 수 없습니다.

근거·검증 추적은 장기 안전 원칙입니다. 현재 MVP에서 RAG·Citation/NLI가 미구현이라는 사실은 이 원칙을 폐기하거나 이미 충족했다는 의미가 아닙니다. 현재 챗봇은 질문 범위를 코드로 제한하지 않으므로 복약 가이드·챗봇의 Production 배포는 차단된 상태입니다.

승인표나 수동 검토만으로 이 차단을 예외 처리하지 않습니다. 조기 검증은 비식별 합성 데이터만 사용하는 접근 통제된 내부 staging 데모로 제한합니다. Production 전환에는 근거·검증 원칙을 구현하거나, 허용 사용자·질문·데이터 범위와 만료를 코드로 강제하는 제한 모드 및 재현 가능한 안전 기준을 별도 보안 ADR·계약·테스트로 승인해야 합니다.

## Post-MVP-1 목표 보존 기본값 — 미적용

| 데이터 | 기본 보존 |
|---|---|
| Terminal Job·Retrieval 실행 메타데이터 | 90일 |
| Publish 완료 Outbox·quarantine·DLQ 메타데이터 | 30일 |
| Idempotency 레코드 | 최소 24시간, 운영 기본값 7일 |
| 사용자에게 보이는 처방·Check-in·감사·Safety 결과 | 계정·사용자 삭제 정책 |

아래 값은 Privacy 승인과 관련 저장·삭제 구현 전까지 Production 기본값으로 적용하지 않습니다. 의료 원문·질문·답변·원문 멱등 키는 목표 구조의 Stream, 일반 로그, quarantine과 DLQ에 저장하지 않습니다. 개인정보·의료 검토나 legal hold가 더 엄격한 조건을 요구하면 그 조건을 우선하고 근거를 Decision에 남깁니다.

Post-MVP-1의 본인 단일 `SELF` profile은 승인된 방향이지만, 사용자당 단일성 제약, 기존 의료 데이터 backfill, FK·migration·cutover·rollback과 endpoint별 권한 테스트가 확정되지 않았습니다. 별도 Decision이 승인될 때까지 Job·결과와 Track B·C, Candidate·Identification 직접 API는 기존 `user_id` 소유권 기준을 유지하고 `profile_id`로 읽기·쓰기를 전환하지 않습니다. 인증 사용자가 직접 소유하지 않은 식별자는 존재 여부를 숨기기 위해 `404`로 응답합니다. 내부 운영자는 감사되는 별도 support role 없이 의료 결과를 조회할 수 없습니다.

보존 기본값과 공개 차단의 정확한 승인 범위는 [Post-MVP-1 외부 승인 게이트](./release-gates/post-mvp-1-external-approvals.md)를 따릅니다.

## Pull Request 확인

- 개인정보 수집·전달 범위가 늘어나는가
- 로그 또는 오류 응답에 민감정보가 남는가
- 새로운 외부 데이터 출처와 라이선스가 기록됐는가
- 현재 변경 범위에 해당하는 OCR·LLM 회귀 테스트 또는 수동 검증 근거가 추가됐는가
- RAG·Citation·Safety·OTC 또는 공식 Identity 변경이라면 승인 Source·Dataset·Rule·Runtime Bundle manifest, 검증 정책과 실행 결과가 함께 갱신됐는가
