# Sync Chat CLOSED_DEMO Retrieval Delta v1 (#180)

> 문서 상태: **Approved Target / CLOSED_DEMO-only composition exception**  
> 구현 상태: Partially implemented — sealed binding, read-only composition, Chat-only
> query authority, payload/prompt delta와 regression coverage를 구현한다. Actual source591
> smoke와 Provider E2E가 끝나기 전에는 READY나 public runtime으로 해석하지 않는다.  
> Decision: [`PD-180-CD-20260921`](../../../governance/decisions/2026-09-21-sync-chat-closed-demo-rag-boundary.md)

## 1. Current 계약과 scope

이 문서는 [`medication-chat-ai-backend.md`](../../current/medication-chat-ai-backend.md)의
current `chat-prompt-v6`/`question`/`history`/`medications` 계약을 변경하지 않는다. 다음
delta는 `CHAT_CLOSED_DEMO_RAG_ENABLED=true`이고 sealed CLOSED_DEMO retrieval이 complete
evidence를 반환한 내부 demo call 하나에만 적용한다. `PUBLIC_TRACK_F`는 계속 false다.

HTTP request/response DTO, ChatSession/ChatMessage persistence, application DB, existing HTTP
201, history, prescription stale 및 consent semantics는 변경하지 않는다.

## 2. Chat query binding authority

authority input은 Pydantic validation을 마친 exact `ChatGenerationInput.question` 한 문자열이다.
history, medications, prior answer, Guide medication name/strength projection, query rewrite,
latest/current lookup은 입력 또는 fallback이 될 수 없다.

| coordinate | frozen value |
| --- | --- |
| HMAC algorithm | `HMAC-SHA-256` |
| preimage version | `closed-demo-chat-query-hmac@1` |
| key namespace | `closed-demo-chat-query-hmac-key@<positive-integer>` |
| verifier artifact | `closed-demo-chat-query-binding-verifier@1.0.0` |
| key Config | `CHAT_CLOSED_DEMO_QUERY_HMAC_KEY`, `CHAT_CLOSED_DEMO_QUERY_HMAC_KEY_VERSION` |

Guide B2 (`guide-query-hmac-key@…`, `guide-query-binding-verifier@1.0.0`)는 Guide medication
authority만 소유하며 이 contract의 input, key, artifact, verifier가 아니다.
`rag_runtime.query_binding`의 redacted text/fingerprint/artifact value type은 policy-neutral
carrier일 뿐 Guide authority가 아니다.

blank/malformed/tampered binding, unavailable key, verifier mismatch는 fail closed다.

## 3. Sealed evidence and Provider payload

runtime은 immutable `sync-chat-closed-demo-17p-binding-v1` only를 load하고 existing 17p
coordinates를 equality-only로 use한다. `latest`, `current`, `MAX`, created-at ordering, first
available index, all members discovery는 금지된다. source591 session is `REPEATABLE READ`,
`READ ONLY`, database `source591_staging`, role `source591_consumer`; no source591 mutation is
permitted.

Provider payload delta is only the all-or-nothing `evidence` array below, after selected hits,
exact chunk hydration, provenance equality and content SHA-256 verification succeed.

```json
{
  "evidence": [{
    "display_order": 1,
    "source_code": "…",
    "source_version": "…",
    "locator": "…",
    "content": "…"
  }]
}
```

No partial evidence, candidate, internal UUID, retrieval receipt, manifest hash, HMAC/fingerprint,
credential, or database metadata is released to the Provider.

## 4. Prompt and observability

When and only when the `evidence` array is non-empty, the exact prompt is
`chat-prompt-v7-closed-demo-evidence`; otherwise the existing exact prompt remains
`chat-prompt-v6`. `ProviderCallDescriptor.prompt_version` for that same call must be the same
identity as the selected prompt. A v7 generation recorded as v6 is a contract failure.

## 5. Fail-closed and excluded work

If any manifest, query binding, embedding, search, eligibility, Evidence Gate, hydration,
provenance or content-hash phase fails, no medical factual Provider generation runs. This target
does not authorize a new retrieval implementation/RRF/Evidence Gate/embedding policy, B2
substitute, Guide carrier, `HybridRetrieveRequest`, DB schema/migration/trigger/RLS, source591
write, or general Backend→Worker boundary expansion.
