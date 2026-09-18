# Load Test Framework (#627)

This directory contains the first load-test framework skeleton for #627. It is intentionally limited to a configurable smoke task so API-specific scenarios can be added after the API surface stabilizes.

## Scope

Included now:

- Locust entrypoint: `load_tests/locustfile.py`
- Auth baseline smoke entrypoint: `load_tests/auth_smoke.py`
- Configurable smoke path with default `/api/openapi.json`
- Optional bearer token injection through environment variable
- CSV result naming guidance
- Repository-side asset validation script and test

Not included now:

- Signup/OCR/Guide/Chat full scenario coverage
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

## Auth Baseline Smoke Command

`load_tests/auth_smoke.py` is the #627 1차-1 Auth minimum smoke scenario. It exercises the smallest reusable authenticated flow:

1. `POST /api/v1/auth/login`
2. `GET /api/v1/users/me`

Use it as a baseline/smoke load, not as a production capacity claim or final `p95 <= 3s` proof.

```bash
mkdir -p docs/validation/load-testing
LOAD_TEST_AUTH_EMAIL="<test-account-email>" \
LOAD_TEST_AUTH_PASSWORD="<test-account-password>" \
uvx locust \
  -f load_tests/auth_smoke.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 5 \
  -r 1 \
  -t 3m \
  --csv docs/validation/load-testing/issue-627-auth-smoke-local
```

Record the commit SHA, target environment, command with secrets redacted, request count, failure count, and p50/p95/max latency with the CSV output.

`GET /api/v1/auth/token/refresh` and `POST /api/v1/auth/logout` are intentionally disabled by default. The current token refresh/logout paths rotate or invalidate token state, so repeated calls from multiple Locust users that share one test account can invalidate other users. Enable `LOAD_TEST_AUTH_INCLUDE_REFRESH=true` or `LOAD_TEST_AUTH_LOGOUT_ON_STOP=true` only for a single-user smoke or for an environment with independent test accounts per Locust user.

### Optional Token Refresh / Logout Smoke

Run refresh/logout only as a single-user smoke unless the environment provides independent test accounts per Locust user:

```bash
LOAD_TEST_AUTH_EMAIL="<test-account-email>" \
LOAD_TEST_AUTH_PASSWORD="<test-account-password>" \
LOAD_TEST_AUTH_INCLUDE_REFRESH=true \
uvx locust \
  -f load_tests/auth_smoke.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 1 \
  -r 1 \
  -t 30s \
  --csv docs/validation/load-testing/issue-627-auth-refresh-local
```

Use `LOAD_TEST_AUTH_LOGOUT_ON_STOP=true` only when the Locust run should explicitly invalidate the test account session at shutdown.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOAD_TEST_SMOKE_PATH` | `/api/openapi.json` | Absolute path used by the smoke task |
| `LOAD_TEST_EXPECT_STATUS` | `200` | Expected HTTP status for the smoke task |
| `LOAD_TEST_BEARER_TOKEN` | empty | Optional bearer token. Do not print or commit the value. |
| `LOAD_TEST_AUTH_EMAIL` | empty | Required by `auth_smoke.py`. Test account email; do not commit real values. |
| `LOAD_TEST_AUTH_PASSWORD` | empty | Required by `auth_smoke.py`. Test account password; do not print or commit. |
| `LOAD_TEST_AUTH_INCLUDE_REFRESH` | `false` | Optional token refresh task. Use only for single-user smoke or independent per-user accounts. |
| `LOAD_TEST_AUTH_LOGOUT_ON_STOP` | `false` | Optional logout call on Locust user shutdown. Use carefully with shared accounts. |

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

1. user/profile read smoke after this Auth baseline
2. medication schedule read smoke
3. check-in and Track C support smoke
4. prescription upload/OCR polling
5. OCR review and prescription confirmation
6. Guide generation
7. Chat session and message flow
8. notifications or Web Push if enabled for the target environment

Each scenario PR should document required fixture setup, authentication state, expected status codes, and whether it is local-only, staging, or production-approved.
