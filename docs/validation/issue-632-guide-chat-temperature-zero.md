# Issue #632 Guide/Chat temperature=0 live variance validation

- 구현 담당자: 송은영 (@phina-io), 권가빈 (@hazelnutflavoured)
- 담당 리뷰어: 정현우 (@ceohwj), Guide/Chat AI 응답 편차·안전 응답 평가 기준
- 기준 브랜치: `develop`
- 검증 일자: 2026-09-16 KST
- 상태: Guide/Chat OpenAI Responses 호출에 `temperature=0` 적용 후 합성 입력 live 30회 반복 측정 완료

## 변경과 평가 범위

이번 검증은 같은 합성 입력을 같은 모델 설정으로 반복 호출했을 때 출력 편차가 #632 완료 기준 안에 들어오는지 확인한다. Guide와 Chat의 OpenAI Responses 호출부에 `temperature=0`을 명시했고, 현재 사용 중인 Responses API 호출 경로에는 `seed` 파라미터를 사용하지 않는다.

평가는 실제 사용자 데이터 없이 합성 입력만 사용한다. 이 문서는 RAG retrieval 편차, Guide/Chat 202 비동기 전환, prompt 대규모 재작성, Production 공개 승인을 다루지 않는다.

## 실행 환경

- model: `gpt-4o`
- Guide prompt/runtime: 현재 `GuideGenerator` + `OpenAIResponsesClient`
- Chat prompt/runtime: 현재 `ChatGenerator` + `OpenAIResponsesClient`
- 반복 횟수: 각 case 30회
- 실행 스크립트: `scripts/measure_guide_chat_output_variance.py`
- live 실행 조건: `RUN_TEMPERATURE_VARIANCE_CHECK=1`, `ENV=local`, 로컬 전용 OpenAI API key
- 입력 데이터: 합성 약명·합성 질문만 사용

API key, credential, 실제 환자 정보, 의료문서 원문은 출력·문서화하지 않았다.

## 1차 관찰과 수정

1차 live 실행에서는 Guide와 Chat safety 모두 실제 출력 자체는 30회 동일했다. 다만 Chat safety 응답이 고정 안전 문구 전체를 큰따옴표로 감싼 형태로 반환되어 기존 exact-match 평가가 실패했다.

- Guide: 30/30 동일, `unique_outputs=1`
- Chat safety: 30/30 동일, `unique_outputs=1`, outer quote 때문에 `rule_match=0/30`
- Chat general: 핵심 사실 30/30, 표면 문구 `unique_outputs=2`

Chat 출력 계약은 JSON/Markdown이 아닌 한국어 평문이므로, 전체 응답을 감싼 outer quote만 제거하도록 `normalize_chat_answer()`를 보정했다. 응답 내부의 일반 문장부호나 내용은 바꾸지 않는다.

## 최종 live 결과

보정 후 같은 스크립트를 다시 실행했다.

| 평가 항목 | 결과 | 비고 |
| --- | ---: | --- |
| Guide rendered content exact match | 30/30 | `unique_outputs=1` |
| Chat fixed safety response rule/exact match | 30/30 | `unique_outputs=1` |
| Chat general answer key fact hit (`저녁 식후`) | 30/30 | `unique_outputs=2` |

Chat general 답변은 핵심 사실인 복용 시점과 의료진·약사 확인 안내를 30회 모두 유지했다. 표면 문구는 두 가지로 갈렸으므로 일반 답변에 대해 exact match 30/30을 주장하지 않는다.

## 실행 명령과 결과

```bash
DB_HOST=127.0.0.1 DB_USER=test DB_PASSWORD=test DB_NAME=test PYTHONPATH=backend:. \
  uv run pytest backend/app/tests/chat_ai/test_schemas.py \
  backend/app/tests/chat_ai/test_client.py \
  backend/app/tests/guide_ai/test_client.py -q
```

결과: `70 passed`

```bash
set -a
. envs/.local.env
set +a
RUN_TEMPERATURE_VARIANCE_CHECK=1 PYTHONPATH=backend:. \
  uv run python scripts/measure_guide_chat_output_variance.py
```

최종 live 결과 요약:

```text
[Guide] n=30 unique_outputs=1
[Chat-safety] rule_match=30/30
[Chat-safety (참고용 전체 분포)] n=30 unique_outputs=1
[Chat-general] key_fact('저녁 식후')_hit=30/30
[Chat-general] n=30 unique_outputs=2
```

## 해석과 남은 범위

- `temperature=0`은 Guide와 Chat safety 고정 응답의 반복 출력 편차를 제거하는 데 충분했다.
- Chat 일반 답변은 핵심 사실은 고정됐지만 표면 문구가 2종으로 남았다. #632 기준에서는 핵심 사실 일치와 처방 모순 없음의 근거로 사용하고, 일반 답변 exact-match 보장으로 해석하지 않는다.
- 이 결과는 합성 입력 기반 live 검증이며 실제 사용자 데이터 처리, Privacy 승인, Production 공개 승인 또는 전체 의료 안전성 보장을 의미하지 않는다.
