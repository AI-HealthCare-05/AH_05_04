# PD-180-CD-20260921 — Sync Chat CLOSED_DEMO production-retrieval composition boundary

| 항목 | 값 |
| --- | --- |
| Decision owner | `@ceohwj` |
| 상태 | **APPROVED / EFFECTIVE IMMEDIATELY** |
| 범위 | #180 Sync Chat CLOSED_DEMO actual RAG 단일 demo composition |
| 비범위 | PUBLIC_TRACK_F, 일반 Production Runtime, Guide runtime |

## 결정

PD-175의 전역 Backend → AI Worker pure-kernel allowlist는 변경하지 않는다. 다만
`backend/app/core/closed_demo_retrieval.py` 한 파일은 sealed CLOSED_DEMO binding을 실제로
읽기 위해 아래의 기존 Worker 모듈을 직접 소비할 수 있다.

```text
ai_worker.adapters.openai_text_embedding
ai_worker.adapters.postgresql_evidence_eligibility
ai_worker.adapters.postgresql_evidence_search
ai_worker.adapters.sqlalchemy_knowledge_chunk_content
ai_worker.tasks.rag.closed_demo_retrieval_binding
ai_worker.tasks.rag.evidence_retrieval
ai_worker.tasks.rag.evidence_search
ai_worker.tasks.rag.production_evidence_gate
ai_worker.tasks.rag.retrieval_runtime
```

`tests/contract/test_backend_ai_worker_import_boundary.py`는 이 파일의 실제 import set이
위 set과 정확히 같음을 강제한다. wildcard, prefix, 미사용 allowlist 항목, 다른 Backend
production 파일의 동일 import는 허용하지 않는다.

## CLOSED_DEMO authority와 data-plane

- `CHAT_CLOSED_DEMO_RAG_ENABLED=false`가 기본값이다. `true`는 명시적 local demo
  environment에서만 허용되며 staging/production Config 기동은 fail closed로 거부한다.
  `PUBLIC_TRACK_F_ENABLED=true`인 instance도 거부한다.
- query authority는 Guide B2와 별개인
  `closed-demo-chat-query-binding-verifier@1.0.0`이다. direct validated
  `ChatGenerationInput.question`만 HMAC preimage
  `closed-demo-chat-query-hmac@1`로 결속한다. history, medications, answer,
  query rewrite 및 Guide medication projection은 입력이 아니다.
- key namespace는 `closed-demo-chat-query-hmac-key@<positive-integer>`이며
  `GUIDE_QUERY_HMAC_KEY*`와 key material, namespace, verifier artifact identity를 공유하지
  않는다. `rag_runtime.query_binding`은 의미 중립 value type만 제공한다.
- retrieval DB는 `source591_staging`, role은 `source591_consumer`만 허용한다.
  session은 `REPEATABLE READ` 및 `READ ONLY`이고 source591 write, schema/migration,
  latest/current discovery는 모두 금지한다.
- sealed manifest의 17p index/snapshot/member coordinates 이외의 evidence는 허용하지
  않는다. 검색, eligibility, Evidence Gate, hydration, provenance 또는 content hash 어느
  하나라도 실패하면 Provider에 medical factual generation을 요청하지 않는다.

## Provider delta

이 Decision은 Current `chat-prompt-v6` 또는 일반 Provider payload 계약을 바꾸지 않는다.
gate가 활성이고 sealed retrieval이 선택·hydrate한 evidence가 있는 경우에만
`chat-prompt-v7-closed-demo-evidence`를 선택한다. 이때 Provider payload에는 다음의
all-or-nothing `evidence` 배열만 추가할 수 있다.

```text
display_order, source_code, source_version, locator, content
```

pre-gate candidate, internal IDs, binding hash, key/fingerprint, DB metadata는 payload에 넣지
않는다. 같은 Provider call의 observability `prompt_version`은 실제 prompt identity와
동일해야 한다(v7 evidence path는 v7, 기존 path는 v6).

ChatSession/ChatMessage persistence는 기존 application DB와 HTTP 201 semantics를 그대로
사용한다. retrieval DB와 application DB를 합치지 않는다.

## 제외와 검증

이 예외는 Guide carrier 재사용, Guide B2 authority 재사용, `HybridRetrieveRequest`,
LangGraph, 새 retrieval/RRF/Evidence Gate/embedding policy, source591 write, new DB
schema/table/trigger/RLS, 일반 Backend→Worker 경계 확장, PUBLIC_TRACK_F 활성화를 승인하지
않는다.

merge/ready 판단은 manifest validation, Chat query binding, source591 read-only smoke,
actual retrieval→Evidence Gate→exact hydration→Provider payload, 기존 Chat HTTP 201/history/
prescription stale/consent 회귀가 모두 증명된 이후에만 할 수 있다.
