# DEV Actual Retrieval Experiment — RET-L / RET-D / RET-H (2026-09-16)

```text
DEV EVIDENCE ONLY — NOT RELEASE PASS
```

- Dataset lifecycle: **DRAFT** (not frozen, `frozen_at` unset)
- HOLDOUT: **NOT PERFORMED** (0 authored, 0 accessed, 0 evaluated)
- Release eligibility: **false**
- 이 문서는 **Production readiness를 의미하지 않는다.**
- 이 문서는 **Clinical validity를 의미하지 않는다.**

This file is a historical DEV experiment log: presentation evidence, a baseline
observation for later retrieval changes, and reproducibility evidence. It is
**not** a Release Gate artifact. Current machine-readable state lives in
`../status.json`; the current-state projection lives in `../report.md`.

---

## 1. Experiment Summary

| Field | Value |
| --- | --- |
| Issue | #273 |
| Related Issue | #178 |
| Experiment type | `ACTUAL_RETRIEVAL_DEV` |
| Experiment ID | `rag-natural-language-retrieval-dev` (`KNOWLEDGE_RETRIEVAL`) |
| Dataset | `rag-natural-language-retrieval-dev@1.0.0` |
| DEV case count | 60 |
| HOLDOUT count | 0 |
| Runner HEAD | `ce802b337bf45dcff9191196db131b8b6afd185b` (clean worktree) |
| Branch | `develop` |
| Environment | `LOCAL` (`environment=LOCAL`, `runtime_eligible=false`) |
| Database | PostgreSQL 17 + pgvector, dedicated evaluation database `ah273_dev_eval`, migrated to single Alembic head `633a1b2c3d4e` |
| Execution window | `2026-09-16T14:57:32Z` – `2026-09-16T14:59:53Z` (UTC) |
| Executed by | `ceohwj` (`GITHUB_LOGIN`, `EVALUATION_IMPLEMENTER`) |
| Adapter | `knowledge-evidence-retrieval.actual.v1` (actual retrieval, not replay) |

승인된 합성 DEV 자연어 질문 60개를 실제 #663 Production Retrieval 경로에 입력하여
RET-L, RET-D, RET-H의 검색 품질, latency, 실패 패턴 및 반복 실행 재현성을 측정하였다.

---

## 2. Experimental Setup

### Knowledge Index

| Field | Value |
| --- | --- |
| Index code / version | `rag-natural-language-retrieval-dev-synthetic-index` / `1.0.0` |
| Index configuration hash | `77a7e63c018cb5c78ce31e35afde3a1d5fe8ca0b4ea1e7307ee61aa605a6b70e` |
| Member count | `100` |
| Embedding model | `openai:text-embedding-3-large` |
| Model version | `text-embedding-3-large` |
| Dimension | `1536` |
| Distance metric | `COSINE` |

**Corpus bootstrap method.** The evaluation database contained no compatible
index, so the 100-statement synthetic corpus was bootstrapped **once** through
the real OpenAI `TextEmbeddingPort` and then **reused unmodified by all six
runs**. The index was not rebuilt between runs.

**Fake embedding usage: none.** `DeterministicFakeEmbeddingAdapter` was not used
for corpus embedding and not used for query embedding. RET-D and RET-H issued
live OpenAI query-embedding calls; RET-L issued none.

No OpenAI API key, credential value, provider response body, or embedding vector
appears anywhere in this document or in any committed artifact.

### Shared binding hashes

| Field | SHA-256 |
| --- | --- |
| Dataset Manifest | `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2` |
| Resource Set | `9359d05ef2753d60de83f997aaffbae04af47792d2812f0e6d3f35405130643d` |
| Evidence Mapping | `e3949bfecebefbd73abf279d6919e13bb42685751d7aa1d96197a15379f6acd5` |
| Partition manifest | `bd813204545083898883d0d4ec8a1de2edbc696d55c917db843fe9436f4c416b` |
| Upstream contract manifest | `f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570` |
| Source snapshot | `rag-natural-language-retrieval-dev-evidence@1.0.0` `e3949bfecebefbd73abf279d6919e13bb42685751d7aa1d96197a15379f6acd5` |

Prompt version `actual-retrieval-v1`; execution-config seed `178`; comparison
policy scope seed `273`.

---

## 3. Variants

```text
RET-L = lexical only
RET-D = dense only
RET-H = lexical + dense + deterministic RRF
```

| Variant | Execution mode | Execution config |
| --- | --- | --- |
| RET-L | `LEXICAL_ONLY` | `rag-natural-language-retrieval-dev-ret-l-v1@1.0.0` |
| RET-D | `DENSE_ONLY` | `rag-natural-language-retrieval-dev-ret-d-v1@1.0.0` |
| RET-H | `HYBRID_RRF` | `rag-natural-language-retrieval-dev-ret-h-v1@1.0.0` |

**RET-HR and any reranker were not part of this experiment.** No reranker stage
was configured, executed, or measured (`reranker_enabled=false`).

---

## 4. Main Results

DEV partition, slice `ALL`, n = 60 cases over 20 independent `transform_origin`
groups, two-sided 95% percentile cluster bootstrap, 10 000 iterations, seed 273.

| Variant | Recall@5 | Precision@5 | MRR | nDCG@5 | No-hit Rate | Median Latency | P95 Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RET-L | `0.4` | `0.08` | `0.4` | `0.4` | `0.6` | Run 1: 12 ms<br>Run 2: 16 ms | Run 1: 29 ms<br>Run 2: 24 ms |
| RET-D | `0.95` | `0.19` | `0.765` | `0.81269` | `0.05` | Run 1: 435 ms<br>Run 2: 393 ms | Run 1: 760 ms<br>Run 2: 637 ms |
| RET-H | `0.95` | `0.19` | `0.848333` | `0.874201` | `0.05` | Run 1: 414 ms<br>Run 2: 452 ms | Run 1: 756 ms<br>Run 2: 582 ms |

**Metric values are shown once per variant because the two repetitions produced
identical metrics.** Metric equality across each variant's run pair was verified
by projection hash (see section 6); only latency differs between runs.

### Numerator / denominator and 95% CI

| Variant | Metric | Value | Numerator / Denominator | 95% CI | Sample cases / groups |
| --- | --- | ---: | ---: | --- | ---: |
| RET-L | Recall@5 | `0.4` | 24 / 60 | `[0.266667, 0.55]` | 60 / 20 |
| RET-L | Precision@5 | `0.08` | 24 / 300 | `[0.053333, 0.11]` | 60 / 20 |
| RET-L | MRR | `0.4` | 24 / 60 | `[0.266667, 0.55]` | 60 / 20 |
| RET-L | nDCG@5 | `0.4` | 24 / 60 | `[0.266667, 0.55]` | 60 / 20 |
| RET-L | No-hit Rate | `0.6` | 36 / 60 | `[0.45, 0.733333]` | 60 / 20 |
| RET-D | Recall@5 | `0.95` | 57 / 60 | `[0.9, 1]` | 60 / 20 |
| RET-D | Precision@5 | `0.19` | 57 / 300 | `[0.18, 0.2]` | 60 / 20 |
| RET-D | MRR | `0.765` | 57 / 60 | `[0.655, 0.866667]` | 60 / 20 |
| RET-D | nDCG@5 | `0.81269` | 57 / 60 | `[0.720224, 0.89543]` | 60 / 20 |
| RET-D | No-hit Rate | `0.05` | 3 / 60 | `[0, 0.1]` | 60 / 20 |
| RET-H | Recall@5 | `0.95` | 57 / 60 | `[0.9, 1]` | 60 / 20 |
| RET-H | Precision@5 | `0.19` | 57 / 300 | `[0.18, 0.2]` | 60 / 20 |
| RET-H | MRR | `0.848333` | 57 / 60 | `[0.74, 0.933333]` | 60 / 20 |
| RET-H | nDCG@5 | `0.874201` | 57 / 60 | `[0.780945, 0.950791]` | 60 / 20 |
| RET-H | No-hit Rate | `0.05` | 3 / 60 | `[0, 0.1]` | 60 / 20 |

Every metric carries `execution_status=COMPLETED`, `decision_status=N/A`,
`decision_basis=DIAGNOSTIC_ONLY`, `threshold=0`. No threshold was created or
approved and no metric yields a PASS/FAIL verdict.

### Recall@5 by declared slice

The slice set is exactly the one declared by the approved evaluation and
comparison policies. No slice was added for this experiment.

| Slice | Cases / groups | RET-L | RET-D | RET-H |
| --- | ---: | ---: | ---: | ---: |
| `EXPRESSION_CANONICAL` | 10 / 10 | `0.6` | `1` | `1` |
| `EXPRESSION_COLLOQUIAL` | 10 / 10 | `0.4` | `0.9` | `0.9` |
| `EXPRESSION_FRAGMENT` | 10 / 10 | `0.5` | `1` | `1` |
| `EXPRESSION_LIMITED_TYPO` | 10 / 10 | `0.6` | `1` | `1` |
| `EXPRESSION_SYNONYM` | 10 / 10 | `0` | `0.8` | `0.8` |
| `EXPRESSION_WORD_ORDER_PARTICLE` | 10 / 10 | `0.3` | `1` | `1` |
| `TOPIC_LIFESTYLE_MANAGEMENT` | 12 / 4 | `0.166667` | `0.916667` | `0.916667` |
| `TOPIC_MEDICATION_INFORMATION` | 12 / 4 | `0.166667` | `0.916667` | `0.916667` |
| `TOPIC_MISSED_DOSE` | 12 / 4 | `0.75` | `1` | `1` |
| `TOPIC_PRECAUTIONS` | 12 / 4 | `0.25` | `0.916667` | `0.916667` |
| `TOPIC_STORAGE` | 12 / 4 | `0.666667` | `1` | `1` |

---

## 5. Run Inventory

Full canonical run UUIDs, read from the published bundles under
`evals/results/`. `verify-result` emits the bundle's
`semantic_content_hash`; a non-empty hash with exit code 0 means the bundle
loaded, re-hashed, and validated cleanly.

| Variant | Run | Run ID | verify-result | Semantic Comparison |
| --- | --- | --- | --- | --- |
| RET-L | 1 | `51429195-8033-4012-bf07-bb48e6f0c46d` | PASS — `15e259fa9c68eb18c96383f6c7827740b305b55ee1d8defa991c63bb3260f74a` | paired with RET-L run 2 |
| RET-L | 2 | `08c171df-cdfe-4d65-bfb7-fecae83be4fd` | PASS — `f79e840455bd65158839a9c92dff89d9dc1d03e9209e569b21fe763916eb11fb` | paired with RET-L run 1 |
| RET-D | 1 | `3263cb0b-c9dd-4699-9137-74a13b78394c` | PASS — `0a9dc15eb738276ff7eb99f51f862865b90946c1fcb7e796fe2f5b08b83b42f2` | paired with RET-D run 2 |
| RET-D | 2 | `9bef77ae-f09c-40f1-a64b-52caca0a00e1` | PASS — `3b4b2c72d4719893792eb68ed0e1a3f5c8bd1cd916a3857199382e5338aff375` | paired with RET-D run 1 |
| RET-H | 1 | `19322b6d-9a86-4ea8-a9f1-335ee72ce4a5` | PASS — `8ab68d536d1e3853542d4bcc7cba436ebc8c7cbacb059a4435a0dc5a56fa2869` | paired with RET-H run 2 |
| RET-H | 2 | `af827615-5849-427b-bb19-21b58f201840` | PASS — `2f3df1a294ae8511d5a621c1669c9c14eba2b2717d0ee039579edcda23c7c973` | paired with RET-H run 1 |

The published `semantic_content_hash` differs inside each pair. That is expected
and is **not** a determinism failure: the global semantic projection still
retains `latency_ms`, so wall-clock variation alone changes it. The global
contract was deliberately left unchanged; a dedicated comparator is used instead
(section 6).

Cross-variant comparison was **not** requested from the standard Comparison
Policy: `validate_retrieval_comparison_pair` rejects a pair whose
`variant_id` values are equal, so the policy does not support same-variant
determinism comparison and `--baseline-run-id` was not used.

---

## 6. Determinism / Reproducibility

Compared with `compare_actual_retrieval_runs`
(`ai_worker/tasks/evaluation/actual_retrieval_determinism.py`), which refuses the
comparison unless variant, dataset code/version, dataset manifest SHA, resource
set hash, resolved evaluation config hash, model config hash, retrieval variant
manifest hash, evaluated partition (`DEV`), case set (all 60 cases), and
Knowledge Index ref are identical. Latency is held out of every equality
projection and reported only as an observation.

| Variant | Retrieval semantic equality | Metric equality | Failure taxonomy equality | Cases compared |
| --- | --- | --- | --- | ---: |
| RET-L | `true` | `true` | `true` | 60 |
| RET-D | `true` | `true` | `true` | 60 |
| RET-H | `true` | `true` | `true` | 60 |

Stable projection hashes (identical for both runs of each variant):

| Variant | Case projection | Metric projection | Failure projection |
| --- | --- | --- | --- |
| RET-L | `1fcfdbaffcc6dfe2b4c933bf4c5ed76f26b3adbd2b7aa96fbabbf897d9317240` | `2f3567e42281ad5d768c84cb9c7dcdeef13fd52b94737894c297f9f0a324965e` | `f76f48152a0fe715cb95ba78657ed966a2bdda8ddfbf46c8fccc004386dfcc10` |
| RET-D | `f73a04da28c61fe87f5759d6561b1dc7cd1add747a7c308292fe9289c5e79d26` | `52cfbc5d3caab4b18237b6ef6e21d700575004fc2392f586e1b6e7d6fd31ee95` | `993c2660b37a4e61165a430a64a27fd18e34378f862e93f8fb9b279528cfbe6d` |
| RET-H | `fbc84f3861c7e027f6bc447f48b1968efa9af55f22ad2e855a51446db218313a` | `237edfac583a276bb7424455cb8e60085150e10768175c13f6ae3c85224234df` | `993c2660b37a4e61165a430a64a27fd18e34378f862e93f8fb9b279528cfbe6d` |

### Observed nondeterminism in prior repetition

An earlier repeat set of the same three variants, executed on commit
`de934d964a90ffaf2db2cfdf21cbb50fca51ad1e` under identical bindings, was **not**
stable at case level:

- **RET-D: 2 of 60 cases varied** — `rag-nlr-dev-020`, `rag-nlr-dev-034`
- **RET-H: 1 of 60 cases varied** — `rag-nlr-dev-036`
- **RET-L: no variation observed**

Those earlier runs predate the metric fix and carried no computed metric values,
so they are not quoted as quality evidence. The instability itself is real and is
recorded here rather than discarded.

```text
The final paired runs were semantically equal, but dense/hybrid
determinism is not considered established because earlier repeated
runs exhibited case-level variation.
```

즉, 최종 pair의 semantic equality는 결정성을 **입증하지 않는다**. RET-L만 관측된
모든 반복에서 안정적이었고, RET-D / RET-H의 결정성은 추가 반복 검증이 필요하다.

---

## 7. Failure Analysis

Final runs: **60/60 cases `execution_status=COMPLETED`** in every run. No case
was blocked by `BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL`; an approved OpenAI
credential was available throughout. The only observed failure taxonomy is:

- failure code: `REQUIRED_EVIDENCE_NOT_IN_TOP_5`
- failure stage: `RETRIEVAL_MISS`

| Variant | Misses | Case IDs |
| --- | ---: | --- |
| RET-L | 36 | `rag-nlr-dev-002`, `-003`, `-004`, `-005`, `-006`, `-007`, `-008`, `-009`, `-010`, `-011`, `-014`, `-015`, `-016`, `-018`, `-019`, `-020`, `-021`, `-022`, `-023`, `-025`, `-026`, `-027`, `-028`, `-030`, `-031`, `-032`, `-033`, `-034`, `-036`, `-038`, `-040`, `-041`, `-046`, `-050`, `-058`, `-059` |
| RET-D | 3 | `rag-nlr-dev-009`, `rag-nlr-dev-022`, `rag-nlr-dev-034` |
| RET-H | 3 | `rag-nlr-dev-009`, `rag-nlr-dev-022`, `rag-nlr-dev-034` |

RET-D and RET-H share the same three residual failures, and all three are also
RET-L failures.

Dense/Hybrid retrieval은 Lexical 대비 top-5 required-evidence miss를 36건에서
3건으로 감소시켰다. 잔여 3건의 근본 원인은 본 실험만으로 확정하지 않고 후속
error analysis 대상으로 유지한다.

---

## 8. Latency Observation

Per-case latency in milliseconds, observed values only. No averaged or derived
figure was invented.

| Variant · run | Count | Min | Median | P95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| RET-L run 1 | 60 | 8 | 12 | 29 | 74 |
| RET-L run 2 | 60 | 10 | 16 | 24 | 77 |
| RET-D run 1 | 60 | 325 | 435 | 760 | 1631 |
| RET-D run 2 | 60 | 313 | 393 | 637 | 1963 |
| RET-H run 1 | 60 | 299 | 414 | 756 | 1456 |
| RET-H run 2 | 60 | 327 | 452 | 582 | 862 |

### Quality / latency trade-off (observed)

| Variant | Recall@5 | nDCG@5 | Median latency range across runs |
| --- | ---: | ---: | --- |
| RET-L | `0.4` | `0.4` | 12–16 ms |
| RET-D | `0.95` | `0.81269` | 393–435 ms |
| RET-H | `0.95` | `0.874201` | 414–452 ms |

Observations:

- RET-L latency is in the tens of milliseconds.
- RET-D and RET-H latency is in the hundreds of milliseconds.
- RET-H was observed with higher MRR and nDCG@5 than RET-D at the same Recall@5.
- RET-D and RET-H include one live OpenAI query-embedding call per case; RET-L
  includes none.
- 현재 표본과 `LOCAL` 환경만으로 production latency SLA를 판단하지 않는다.

---

## 9. Presentation Takeaways

- 실제 자연어 DEV 60개에서 Lexical(RET-L) Recall@5는 `0.40`이었다.
- Dense(RET-D)와 Hybrid(RET-H)는 Recall@5 `0.95`를 기록했고, top-5 miss는 36건에서
  3건으로 줄었다.
- Hybrid는 Dense와 동일한 Recall@5를 유지하면서 MRR(`0.848333` vs `0.765`)과
  nDCG@5(`0.874201` vs `0.81269`)가 더 높게 관찰됐다.
- Dense/Hybrid의 latency는 Lexical 대비 median 기준 수십 ms에서 수백 ms로 크게
  증가했다.
- 이전 반복에서 Dense/Hybrid의 case-level 변동이 관찰되어 결정성은 추가 검증이
  필요하다.

이 관찰값은 `DRAFT` 합성 DEV dataset의 diagnostic 측정이며, 어떤 variant의 최종
채택이나 Production 적합성 판단도 포함하지 않는다.

---

## 10. Provenance

| Field | Value |
| --- | --- |
| Runner HEAD | `ce802b337bf45dcff9191196db131b8b6afd185b` |
| Dataset Manifest SHA-256 | `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2` |
| Resource Set SHA-256 | `9359d05ef2753d60de83f997aaffbae04af47792d2812f0e6d3f35405130643d` |
| Evidence Mapping SHA-256 | `e3949bfecebefbd73abf279d6919e13bb42685751d7aa1d96197a15379f6acd5` |
| Partition manifest hash | `bd813204545083898883d0d4ec8a1de2edbc696d55c917db843fe9436f4c416b` |
| Upstream contract manifest hash | `f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570` |
| Knowledge Index configuration hash | `77a7e63c018cb5c78ce31e35afde3a1d5fe8ca0b4ea1e7307ee61aa605a6b70e` |
| RET-L resolved evaluation config hash | `50fcfb0e77b35bf564e7a509640cdf75fb897160fbb9ff5982e01cae84b06c86` |
| RET-D resolved evaluation config hash | `bdfc88ac458ba60b1888176a8d73d37229782db8c62db0dd302533a8d6f6ef94` |
| RET-H resolved evaluation config hash | `8aa54a3c50dccd5e18cc15bbe4e1ab62bd56f09f44220c02f4fc8197a2ff6c02` |
| RET-L retrieval variant manifest hash | `ef805593a3d0e5a659fc64a99e3466fe0f6eb337479a6e743c69af40af97b50b` |
| RET-D retrieval variant manifest hash | `7fe700c005600a9fecbf2aae6019dc1700e3ad984dbd3862060439cf9b3dd991` |
| RET-H retrieval variant manifest hash | `e382919cc10a3dc1b9f5573d6e35178959d5a6b1405b824e33543a7573981411` |
| RET-L model config hash | `3327da49db4fd35cbd381285847bbd6d07e2ae2e11a8a2d568277df64e9daf94` |
| RET-D / RET-H model config hash | `63f272aa325044d80cb5028a77ef7122212dba82a3767a753963f26db32c0bb8` |

`resolved_evaluation_config_hash` includes `runner_commit_sha` in its canonical
preimage (`ai_worker/tasks/evaluation/config.py`), so each result above is bound
to the runner HEAD named in this table.

---

## 11. Known Limitations

1. `actual_run_ref` hash semantics are **not specified by the contract**. The
   status file records a single reference, and the value used is the RET-H run 1
   `result_content_manifest_hash`
   (`228ca8ad3ad47d12888801e8a114a93484c80ebc41ca4a7a32127fce075e4f79`), chosen as
   the representative production-shaped variant. This choice needs reviewer
   confirmation.
2. Receipt-level diagnostics have no published seam in the standard Run Bundle.
   No new Schema Set was created for this work.
3. Search receipt hash and query embedding SHA-256 pair comparison was therefore
   **not performed**, even though the adapter collects both internally.
4. Dense/hybrid determinism is **not established** (section 6).
5. HOLDOUT was **not executed** — 0 authored, no access authorization, no Freeze.
6. Protected Retrieval Runner (#368) and HOLDOUT Freeze remain **incomplete**;
   `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`,
   `WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION` and `WAITING_FOR_HOLDOUT_FREEZE`
   all remain in force.
7. **DEV results are not a Release PASS**, not Production readiness, and not
   clinical validity. `release_eligible` stays `false` and the dataset stays
   `DRAFT`.

---

## 12. Verification

Executed on runner HEAD `ce802b337bf45dcff9191196db131b8b6afd185b`.

| Check | Command | Result |
| --- | --- | --- |
| Actual retrieval | `uv run pytest ai_worker/tests/evaluation/test_actual_retrieval.py -q` | 10 passed |
| Actual retrieval index | `uv run pytest ai_worker/tests/evaluation/test_actual_retrieval_index.py -q` | 4 passed |
| Retrieval metrics | `uv run pytest ai_worker/tests/evaluation/test_retrieval_metrics.py -q` | 34 passed |
| Evaluation suite | `uv run pytest ai_worker/tests/evaluation -q` | 1457 passed |
| RAG unit tests | `uv run pytest ai_worker/tests/rag -q` | 2002 passed |
| RAG integration | `uv run pytest tests/integration/rag -q` | 206 passed, 128 skipped |
| Ruff lint | `uv run ruff check <changed paths>` | All checks passed |
| Ruff format | `uv run ruff format <changed paths> --check` | 8 files already formatted |
| Mypy | `uv run mypy ai_worker/tasks/evaluation` | Success: no issues found in 50 source files |
| Whitespace | `git diff --check` | clean |

`tests/integration/rag` requires database environment variables; without them the
suite fails at collection rather than running. It was executed against the
dedicated `ah273_dev_eval` database.

---

## 13. Raw Artifact Policy

```text
Raw Run Bundles:
evals/results/<run-id>/

Repository tracking:
NOT TRACKED / gitignored

The Markdown experiment log stores only non-sensitive aggregate
results, identifiers, hashes, and reproducibility evidence.
```

This log contains no API key, no provider response body, no embedding vector, no
database dump, and no real patient or prescription data. HOLDOUT question text
and Gold content are absent because HOLDOUT was never authored or accessed.

---

## Re-running this experiment

Do not overwrite this file after a retrieval configuration change. Add a new
dated log beside it so that presentations and retrospectives can compare results
in chronological order:

```text
experiments/
├── 2026-09-16-dev-actual-retrieval-ret-l-d-h.md
├── <YYYY-MM-DD>-dev-actual-retrieval-after-<change>.md
└── ...
```
