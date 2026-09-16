# Issue #632 Guide/Chat temperature=0 live variance validation

- 구현 담당자: 송은영 (@phina-io)
- 담당 리뷰어: 정현우 (@ceohwj), Guide/Chat AI 응답 편차·안전 응답 평가 기준
- 기준 브랜치: `develop`
- 검증 일자: 2026-09-16 KST
- 상태: Guide/Chat OpenAI Responses 호출에 `temperature=0` 적용 후 합성 입력 live 30회 반복 측정 완료

## 변경과 평가 범위

이번 검증은 같은 합성 입력을 같은 모델 설정으로 반복 호출했을 때 출력 편차가 #632 완료 기준 안에 들어오는지 확인한다. Guide와 Chat의 OpenAI Responses 호출부에 `temperature=0`을 명시했고, 현재 사용 중인 Responses API 호출 경로에는 `seed` 파라미터를 사용하지 않는다.

평가는 실제 사용자 데이터 없이 합성 입력만 사용한다. 이 문서는 RAG retrieval 편차, Guide/Chat 202 비동기 전환, prompt 대규모 재작성, Production 공개 승인을 다루지 않는다.

## 실행 환경

- configured model: `gpt-4o`
- returned model: `gpt-4o-2024-08-06`
- Guide prompt/runtime: 현재 `GuideGenerator` + `OpenAIResponsesClient`
- Chat prompt/runtime: 현재 `ChatGenerator` + `OpenAIResponsesClient`
- 반복 횟수: 각 case 30회
- 실행 스크립트: `scripts/measure_guide_chat_output_variance.py`
- live 실행 조건: `RUN_TEMPERATURE_VARIANCE_CHECK=1`, `ENV=local`, `OPENAI_MODEL=gpt-4o`, 로컬 전용 OpenAI API key
- 입력 데이터: 합성 약명·합성 질문만 사용

API key, credential, 실제 환자 정보, 의료문서 원문은 출력·문서화하지 않았다. 스크립트는 configured model이 `gpt-4o`가 아니거나 Provider 반환 모델명이 `gpt-4o` 계열이 아니면 중단한다.

## 1차 관찰과 수정

1차 live 실행에서는 Guide와 Chat safety 모두 실제 출력 자체는 30회 동일했다. 다만 Chat safety 응답이 고정 안전 문구 전체를 큰따옴표로 감싼 형태로 반환되어 기존 exact-match 평가가 실패했다.

- Guide: 30/30 동일, `unique_outputs=1`
- Chat safety: 30/30 동일, `unique_outputs=1`, outer quote 때문에 `rule_match=0/30`
- Chat general: 당시 predicate가 `"저녁"` 포함만 확인해 evidence가 약했음

최종 수정에서는 production `normalize_chat_answer()`에서 outer quote를 제거하지 않는다. 측정 스크립트의 Chat safety scorer에서만, wrapper를 제거한 값이 승인된 exact safety 문구와 일치할 때 한정해 wrapper를 제거하고 평가한다. 일반 사용자 인용과 history answer는 보존한다.

Chat general predicate는 다음 세 조건을 모두 확인하도록 강화했다.

1. 실제 처방 복용 시점인 `저녁 식후` 포함
2. 이 case에서 요구하는 의료진·약사 확인 안내 포함
3. `아침 식후`, `점심 식후`, `공복`, `식전`, `저녁 식후가 아니라` 등 처방 모순 표현 없음

## 최종 live 결과

강화된 기준으로 같은 스크립트를 다시 실행했다.

| 평가 항목 | 결과 | 비고 |
| --- | ---: | --- |
| Guide rendered content exact match | 30/30 | `unique_outputs=1`, returned model `gpt-4o-2024-08-06` |
| Chat fixed safety response rule/exact match | 30/30 | `unique_outputs=1`, returned model `gpt-4o-2024-08-06` |
| Chat general timing/action/no-contradiction | 30/30 | `unique_outputs=3`, returned model `gpt-4o-2024-08-06` |

Chat general 답변은 `저녁 식후` 복용 시점, 의료진·약사 확인 안내, 처방 모순 없음 조건을 30회 모두 만족했다. 표면 문구는 세 가지로 갈렸으므로 일반 답변에 대해 exact match 30/30을 주장하지 않는다.

## 실행 명령과 결과

```bash
DB_HOST=127.0.0.1 DB_USER=test DB_PASSWORD=test DB_NAME=test PYTHONPATH=backend:. \
  uv run pytest backend/app/tests/chat_ai/test_schemas.py \
  tests/scripts/test_measure_guide_chat_output_variance.py \
  backend/app/tests/chat_ai/test_client.py \
  backend/app/tests/guide_ai/test_client.py -q
```

결과: `72 passed`

```bash
set -a
. envs/.local.env
set +a
RUN_TEMPERATURE_VARIANCE_CHECK=1 PYTHONPATH=backend:. \
  uv run python scripts/measure_guide_chat_output_variance.py
```

최종 live 결과 요약:

```text
[Model] configured=gpt-4o
[Guide] n=30 unique_outputs=1
[Guide-model] n=30 unique_outputs=1
  count=30: 'gpt-4o-2024-08-06'
[Chat-safety] rule_match=30/30
[Chat-safety (참고용 전체 분포)] n=30 unique_outputs=1
[Chat-safety-model] n=30 unique_outputs=1
  count=30: 'gpt-4o-2024-08-06'
[Chat-general] timing_action_no_contradiction=30/30
[Chat-general] n=30 unique_outputs=3
[Chat-general-model] n=30 unique_outputs=1
  count=30: 'gpt-4o-2024-08-06'
```

## 해석과 남은 범위

- `temperature=0`은 Guide와 Chat safety 고정 응답의 반복 출력 편차를 제거하는 데 충분했다.
- Chat general은 핵심 predicate는 30/30 통과했지만 표면 문구가 3종으로 남았다. #632 기준에서는 복용 시점·행동 안내·처방 모순 없음의 근거로 사용하고, 일반 답변 exact-match 보장으로 해석하지 않는다.
- 이 결과는 합성 입력 기반 live 검증이며 실제 사용자 데이터 처리, Privacy 승인, Production 공개 승인 또는 전체 의료 안전성 보장을 의미하지 않는다.
