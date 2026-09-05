# MFDS P0 Endpoint validation

Issue #155 validates the three P0 MFDS operations from Local only. The probe
never writes provider response bodies, authenticated URLs, or API keys.
Checked-in fixtures under `tests/fixtures/rag/mfds/` are synthetic and contain
no provider records.

## Local execution

Use the Decoding form of the public-data service key and keep it outside the
repository.

```bash
read -r -s "RAG_MFDS_API_KEY?MFDS Decoding key: "
echo
export RAG_MFDS_API_KEY
```

A completed Receipt can only be written from a full scan:

```bash
RAG_MFDS_LIVE_VALIDATION=1 \
PYTHONPATH="$PWD/backend:$PWD" \
uv run python -m ai_worker.tasks.rag.source_client.probe \
  --operation LIST_APPROVED_PRODUCTS \
  --output-dir docs/validation/rag/endpoints \
  --full-scan \
  --write-receipt
```

Replace the operation with
`LIST_INGREDIENT_CONTRAINDICATIONS` or
`LIST_PATIENT_MEDICATION_GUIDES` for the other P0 endpoints. A failed
full-scan suitability check still writes a sanitized failed Receipt and exits
non-zero.

Without the exact Local opt-in, the command makes no provider call and writes a
`NOT_RUN` Receipt.

## Observed 2026-09-05 result

| Operation | Rows | Primary-key observation | Endpoint parser gate |
| --- | ---: | --- | --- |
| `LIST_APPROVED_PRODUCTS` | 42,989 | `ITEM_SEQ`: null 0, duplicate 0 | allowed at the endpoint gate |
| `LIST_INGREDIENT_CONTRAINDICATIONS` | 1,836 | candidate null 0, duplicate 469; exact duplicate rows 1 | blocked |
| `LIST_PATIENT_MEDICATION_GUIDES` | 4,782 | `itemSeq`: null 0, duplicate 17; exact duplicate rows 0 | blocked |

The DUR and patient-guide endpoints are reachable and their pagination
boundaries pass. They are blocked because the provider payload does not expose
a stable unique natural key. The client preserves this as a fail-closed
`SCHEMA_DRIFT` result instead of inventing an identifier or silently dropping
rows.

Product endpoint suitability does not activate the public RAG runtime. Parser
canonicalization and immutable Source Snapshot work remain owned by #165, and
Catalog lifecycle work remains owned by #166. External Source approval and the
repository release gates must also pass before runtime activation.
