# #633 피드백 검토 → 합성 Gold → 회귀 평가 절차 초안

상태: **운영안 채택 / Local 구현 검증 중**. 실제 사용자 수집·검토·Provider 평가 실행 증거는 별도다.
[PD-633](../governance/decisions/2026-09-16-guide-chat-feedback-633.md)과
[피드백 계약 후보](../contracts/proposed/guide-chat-feedback-v1.md)를 함께 검토한다.

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
실행하지 않은 항목은 `NOT_RUN`, 승인 미확보는 승인 대기로 명시한다.
응급·중복·과량 안전 gate가 실패하면 일반 품질 점수가 올라도 개선 후보를 채택하지 않는다.

## #633 완료 증빙

| 단계 | 필요한 증빙 | 현재 상태 |
|---|---|---|
| 저장·API·UI | migration·실제 DB/API·브라우저 테스트 | Local 구현·통과, 실사용 승인 별도 |
| 검토 | 승인된 접근·정책과 부정 피드백 처리 기록 | 미확보 |
| 합성 Gold 편입 | 내부 연결 기록, 합성 case·버전·hash·책임 리뷰 | 31-case 합성 데모 추가, 책임 리뷰 대기 |
| 개선 활용 | 동일 평가셋의 prompt 전후 결과와 안전·사람 검토 | 미실행 |

#581의 기존 v5 30-case와 비교 설정만으로 #633의 신규 피드백 수집·개선 루프를 완료 처리하지 않는다.
실제 검토 사례가 없으면 합성 데모의 저장→변환→평가 연결과 실제 운영 실적을 분리해 보고한다.

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
