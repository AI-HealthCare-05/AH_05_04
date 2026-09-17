# Load Test Framework (#627)

This directory contains the first load-test framework skeleton for #627. It is intentionally limited to a configurable smoke task so API-specific scenarios can be added after the API surface stabilizes.

## Scope

Included now:

- Locust entrypoint: `load_tests/locustfile.py`
- Configurable smoke path with default `/api/openapi.json`
- Optional bearer token injection through environment variable
- CSV result naming guidance
- Repository-side asset validation script and test

Not included now:

- Full login/signup/OCR/Guide/Chat scenario coverage
- Real device, real patient, or real prescription traffic
- Production capacity claim or `p95 <= 3s` evidence
- Secret provisioning, monitoring dashboards, or alert thresholds

## Local Smoke Command

```bash
mkdir -p docs/validation/load-testing
uvx locust \
  -f load_tests/locustfile.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 1 \
  -r 1 \
  -t 30s \
  --csv docs/validation/load-testing/issue-627-framework-smoke-local
```

The default smoke path is `/api/openapi.json`, matching the deployment runbook's FastAPI HTTP liveness check. Do not use `/api/v1/health` as a default target because the production runbook states that route is not an app liveness endpoint.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOAD_TEST_SMOKE_PATH` | `/api/openapi.json` | Absolute path used by the smoke task |
| `LOAD_TEST_EXPECT_STATUS` | `200` | Expected HTTP status for the smoke task |
| `LOAD_TEST_BEARER_TOKEN` | empty | Optional bearer token. Do not print or commit the value. |

## Result Artifacts

Store run outputs under `docs/validation/load-testing/` or an approved private evidence location. Commit only non-sensitive summaries. Do not commit access tokens, cookies, real patient data, prescription images, OCR raw text, provider payloads, or full server logs.

Recommended summary fields for future scenario runs:

- issue and PR number
- target environment and base URL category, without secrets
- commit SHA
- run command, with secrets redacted
- user count, spawn rate, duration
- scenario list and excluded scenarios
- request count, failure count, p50, p95, max latency
- known local/CI/production limitations

## Future API Scenario Expansion

After API paths stabilize, add scenarios in small groups rather than one broad PR:

1. auth/profile
2. prescription upload/OCR polling
3. OCR review and prescription confirmation
4. Guide generation
5. Chat session and message flow
6. notifications or Web Push if enabled for the target environment

Each scenario PR should document required fixture setup, authentication state, expected status codes, and whether it is local-only, staging, or production-approved.
