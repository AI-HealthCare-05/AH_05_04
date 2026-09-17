# #633 피드백 검토 → 합성 Gold → 회귀 평가 절차 초안

상태: **운영안 채택 / Local 구현·제품/운영 기준 병합 완료 / AI-RAG 책임 리뷰 중**. 실제 사용자 수집·Production 공개 증거는 별도다.
[PD-633](../governance/decisions/2026-09-16-guide-chat-feedback-633.md)과
[피드백 계약 후보](../contracts/proposed/guide-chat-feedback-v1.md)를 함께 검토한다. PR #638은 저장·API·Local UI·합성 검증 기반을 병합했지만 #633 전체 종료를 의미하지 않는다.

## PR 1 제품/운영 결정 범위

이번 정리의 책임 확인자는 권가빈이다. 확인 대상은 코드 동작 변경이 아니라 #638로 들어간 Local feedback 수집 기반을 제품·운영 기준으로 소비할 수 있는지다.

- 피드백 UI는 완료된 AI 응답에 한정하고, 새로고침 뒤 기존 선택 복원은 이번 기준에 포함하지 않는다.
- `POSITIVE`는 품질 신호로 집계할 수 있지만 자동 개선 또는 의료 정답 승인으로 사용하지 않는다.
- `NEGATIVE`는 검토 queue 진입 신호이며, 검토 전에는 prompt·dataset·안전 정책 변경 근거가 아니다.
- 자유 의견은 민감정보가 포함될 수 있으므로 원문을 GitHub, Discord, 일반 로그, Provider payload, 평가 artifact에 복사하지 않는다.
- #633 완료 판단은 “피드백 저장 구조와 개선 활용 절차가 존재한다”는 평가 기준 3-4 대응에 한정한다. 실제 운영 공개, 실제 사용자 피드백 실적, 반복 prompt 개선 완료는 Production 공개 gate에서 별도로 판단한다.

PR #730 병합으로 제품/운영 기준은 정리됐다. PR #740은 합성 Gold 기대·금지 응답, deterministic replay, 안전 gate 보존 기준을 정리한다. 다만 PR #730에서 남긴 동일 평가셋 prompt 전후 비교와 안전·사람 검토는 아직 실행하지 않았으므로 #633 완료 증빙으로 대체하지 않는다.

## 부정 피드백 검토

1. 권가빈이 주 1회 부정 피드백을 검토하고 송은영이 승인된 접근 경로·감사·보존·삭제 통제를 확인한다.
   현재 별도 support role이 없으므로 개발자라는 이유만으로 의료 결과를 조회하지 않는다.
2. 승인된 환경에서 `rating=NEGATIVE`를 `updated_at, id` 순으로 확인한다.
   외부 관리 대시보드·일반 로그·GitHub Issue에 원본 의견·대화를 복사하지 않는다.
3. 제한된 검토 기록에 feedback id·검토한 updated_at·실패 유형·처리 판단·검토자·시각을 남긴다.
   feedback 수정으로 rating이나 updated_at이 바뀌면 이전 검토를 최신 검토로 오인하지 않는다.
   사용자 평가만으로 의료 정답·위험 여부를 확정하지 않는다. 정현우가 안전 의미를 검토한다.
4. 검토 불필요·중복·정보 부족·합성 사례 작성 대상으로 판단 이유를 기록한다.
   이 기록의 접근·보존 정책은 원본과 함께 승인받는다. 새 runtime 검토 상태 enum은 도입하지 않는다.

## 합성 사례 작성과 연결

권가빈이 실패 유형만 추출해 사람·약명·수치·시각·상황을 새로 구성한 합성 입력과 기대·금지 응답을
작성한다. 실제 원문에서 이름만 지우는 방식을 충분한 비식별화로 간주하지 않는다.
원문을 외부 LLM에 전송해 요약·변환하지 않는다.

운영 내부 기록에서만 feedback id와 synthetic case id를 연결한다. 저장소에는 synthetic case id,
실패 유형, dataset id/version, 실제 승인된 경우에만 승인자·시각·근거를 기록한다.
실제 UUID·사용자 정보·의견·의료 원문 또는 이를 유추할 수 있는 원문 hash는 공개 provenance에 넣지 않는다.
신규 수집을 흉내 낸 fixture는 `SYNTHETIC_DEMO`로 표시하며 실제 사용자 검토 사례라고 보고하지 않는다.

Chat은 `evals/generation/chat-v5-short-followup-eval-v1.json`의 기존 30 case를 보존한 새 버전을
만든다. 기존 schema의 question/history/medications, expected와 금지 조건을 활용한다.
Guide는 `evals/generation/guide-v3-eval-v1.json` 기반의 별도 회귀 사례로 관리한다.
Guide는 제한 생성·Backend renderer 계약이 있으므로 자유 의료 답변용 Chat 기대 문장을 그대로 이식하지 않는다.
공통 실패 유형과 검토 기록으로 연결하되 각 제품의 생성·안전 계약에 맞게 검증한다.

정현우의 기대·금지 응답 검토와 권가빈의 비식별·제품 확인 후 새 dataset version/hash를 동결한다.
기존 canonical 파일을 덮어쓰거나 synthetic 표지만 붙여 승인 없이 live allowlist를 우회하지 않는다.

## 개선 전후 평가

현재 `app.evaluation.chat_history_runner` deterministic 모드는 저장된 replay 출력의 판정기 검증이다.
또한 `baseline/history`는 동일 prompt에서 history 유무 비교다. 둘 모두 prompt 개선 증거를 대체하지 않는다.

기존 `app.evaluation.chat_blind_ab_runner`와
`evals/generation/chat-conversation-quality-blind-ab-v1.json`을 재사용하되,
새 dataset에 맞는 별도 버전 설정과 canonical hash 검증을 코드·테스트에서 함께 갱신한다.
동일 dataset·model·설정으로 이전 prompt와 후보 prompt를 비교한다. 모델도 바꾸면 prompt 효과와 구분해 보고한다.

실행은 승인된 합성 Local opt-in만 허용하고 기존 history·Privacy·Production gate를 유지한다.
결과에는 dataset·prompt version/hash, source commit, model·설정, case별 결과,
Provider 호출 여부·latency·token, safety gate, blind review와 unblind 결과를 연결한다.
실행하지 않은 운영 검증 항목은 별도 상태로 명시한다. deterministic replay는 합성 fixture와 판정기 보존 증빙이며 prompt 전후 비교나 사람 검토를 대체하지 않는다.
응급·중복·과량 안전 gate가 실패하면 일반 품질 점수가 올라도 개선 후보를 채택하지 않는다.

## 후속 evidence PR 기준

#633 후속 evidence PR은 정현우 책임 리뷰어가 확인한 아래 기준을 따른다.

1. `evals/generation/chat-feedback-gold-v1.json`의 synthetic Gold 31-case를 동일 비교 평가셋으로 사용한다. 실제 사용자 feedback 원문은 사용하지 않는다.
2. 31-case는 Chat prompt 개선용 Gold이며 기존 Track F RAG Gold와 목적을 분리해 기록한다.
3. 같은 31-case에서 feedback 반영 전 prompt와 현재 prompt를 비교한다. Dataset, model, temperature, max tokens, timeout, medication/history fixture를 고정하고 prompt version/hash만 달라지는 paired comparison으로 실행한다.
4. 비교 항목은 기대 응답 충족, 금지 응답 위반, safety violation, 문맥 대상 식별, 불필요한 재질문, 처방 모순 등 현재 Gold에 정의된 품질 기준으로 둔다.
5. deterministic replay만으로는 prompt 전후 비교 evidence로 보지 않는다. baseline/candidate 결과가 각각 생성되어야 한다.
6. 기존 `chat-conversation-quality-blind-ab-v1.json`의 prompt variant 비교 구조를 재사용하고 새 평가 프레임워크를 만들지 않는다.
7. RAG 전후 비교용 Gold는 #159 및 기존 Retrieval/HOLDOUT Gold 체계와 분리한다. #633에서 새로 만들거나 31-case와 합치지 않는다.
8. 기존 Gold Conversation 안전 기준과 #632 direct Guide/Chat Provider 경로의 live safety/variance evidence는 supporting evidence로 연결할 수 있다. #632를 RAG 전후 성능 evidence로 표현하지 않는다.
9. 이번 31-case baseline/candidate 비교에서 emergency·duplicate/overdose·금지 응답 관련 safety regression이 없음을 확인한다. 별도 대규모 의료 safety dataset을 추가하지 않는다.
10. 사람 검토는 전체 31-case 수동 PASS/FAIL 재작성 대신 dataset version/hash, baseline/candidate prompt version/hash, 통제 실행 조건, case별 기계 판정 결과 또는 failure list, safety regression 결과, 전체 비교 요약 artifact와 정현우 책임 리뷰 승인 코멘트로 연결한다.
11. `guide-chat-feedback-v1`은 Proposed 유지가 맞다. Current 승격은 #633 Close 필수 조건이 아니며, 운영 공개 승인 시 별도 승격한다.

위 기준을 충족하기 전에는 deterministic replay만으로 #633 완료 또는 Current 승격을 주장하지 않는다.

고정된 실행 입력은 다음과 같다.

- comparison config: `evals/generation/chat-feedback-gold-prompt-comparison-v1.json`
- dataset: `evals/generation/chat-feedback-gold-v1.json`
- baseline prompt snapshot: `evals/generation/prompts/chat-prompt-v4.txt`
- current prompt snapshot: `evals/generation/prompts/chat-prompt-v5.txt`
- 실행 구조: 기존 `app.evaluation.chat_blind_ab_runner`의 `run`/`unblind` command

후속 evidence PR의 산출물은 아래 경로를 사용한다.

- review packet: `docs/validation/issue-633-feedback-prompt-comparison-review-packet.json`
- private assignment artifact: 공개 저장소에 커밋하지 않고 승인된 제한 접근 위치에 보관
- judgment template: `docs/validation/issue-633-feedback-prompt-comparison-judgment-template.json`
- unblind summary: `docs/validation/issue-633-feedback-prompt-comparison.json`
- human review summary: `docs/validation/issue-633-feedback-prompt-comparison.md`

## #633 완료 증빙

| 단계 | 필요한 증빙 | 현재 상태 |
|---|---|---|
| 저장·API·UI | migration·실제 DB/API·브라우저 테스트 | Local 구현·통과, 실사용 승인 별도 |
| 검토 | 승인된 접근·정책과 부정 피드백 처리 기록 | PR #730에서 Local 합성 demo 기준의 제품·운영 처리 기준 정리; 실제 사용자 검토 기록은 Production 공개 전 별도 |
| 합성 Gold 편입 | 내부 연결 기록, 합성 case·버전·hash·책임 리뷰 | 31-case 합성 demo와 provenance 고정; PR 2에서 현우 책임 리뷰 대상 |
| 개선 활용 | 동일 평가셋의 prompt 전후 결과와 안전·사람 검토 | NOT_RUN. PR #740은 deterministic replay 31/31·safety violation 0만 재확인하며 prompt 전후 비교·사람 검토 evidence가 아니다. |

기존 Chat 품질 평가와 비교 설정만으로 #633의 신규 피드백 수집·개선 루프를 완료 처리하지 않는다.
#638의 31-case 합성 demo는 저장된 NEGATIVE feedback을 비식별 합성 Gold 후보로 연결하고, replay artifact로 기대·금지 응답과 안전 gate 보존을 검증한다. 일반 Chat 대화 품질 개선 이슈는 별도로 유지하며, #633은 feedback 수집·합성 Gold 연결·검증 기준 정렬에 한정한다. 실제 운영 실적은 Production 공개 gate에서 별도로 다루지만, prompt 전후 비교·안전/사람 검토가 완료되기 전에는 #633 전체 완료로 보지 않는다.

## 채택한 보존·실행 안내

각 feedback은 최초 제출부터 최대 30일, 검토 목적 달성 후 불필요하면 조기 삭제한다.
수정 시 created_at을 유지하며 내부 원본 연결 기록은 원본 삭제 시 함께 제거한다.
사용자는 해당 결과의 피드백 삭제 버튼 또는 같은 target DELETE API로 조기 삭제를 요청할 수 있다.
합성 사례는 실제 원문·식별자와 독립된 경우에만 장기 보관한다.

합성 Local DB에서 하루 1회 다음 명령을 실행한다. 비밀값은 승인된 환경 주입 수단을 사용한다.

```bash
PYTHONPATH=backend:. uv run --env-file envs/.local.env python -m app.commands.purge_feedback
```

실제 scheduler는 이번 작업에서 설치하지 않았다. 서비스 장기 기동 전 운영 담당자가 일일 실행·실패 알림을
구성해야 한다. 만료 삭제의 최장 실행 지연과 백업 파기 정책까지 확인하기 전 실사용 수집을 허용하지 않는다.
최대 30일 정책에 대해 일일 batch만으로 정확히 만료 순간 삭제가 보장되는 것은 아니며,
배포 시 삭제 실행 주기·기한 관리 근거가 필요하다.

운영 검토 접근 권한·감사 수단은 아직 구축하지 않았다. 이번 Local 검증은 합성 데이터만 사용한다.
실사용 검토자에게 일반 DB 계정이나 원문 export를 제공하는 우회 운영을 하지 않는다.

구현·합성 실행 결과는 [#633 검증 기록](../validation/issue-633-feedback.md)을 따른다.
