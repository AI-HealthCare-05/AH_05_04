# Load Test Framework (#627)

This directory contains the first load-test framework skeleton for #627. It is intentionally limited to configurable smoke and baseline scenarios so API-specific scenarios can grow after the API surface stabilizes.

## Scope

Included now:

- Locust entrypoint: `load_tests/locustfile.py`
- Auth baseline smoke entrypoint: `load_tests/auth_smoke.py`
- OCR / Worker smoke scenario: `load_tests/ocr_worker_smoke.py`
- Medication schedule read smoke entrypoint: `load_tests/schedule_smoke.py`
- Configurable smoke path with default `/api/openapi.json`
- Optional bearer token injection through environment variable
- CSV result naming guidance
- Repository-side asset validation script and test

Not included now:

- Full login/signup/OCR/Guide/Chat scenario coverage
- Schedule create/update/cancel/check-in load scenarios
- OCR Provider capacity approval or final `p95 <= 3s` claim
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

## Medication Schedule Read Smoke Command

`load_tests/schedule_smoke.py` is the #743 1차-3 medication schedule read smoke scenario. It exercises a read-only authenticated flow:

1. `POST /api/v1/auth/login`
2. `GET /api/v1/medication-occurrences?date=YYYY-MM-DD`
3. `GET /api/v1/medication-occurrences/{occurrence_id}/medication`

```bash
mkdir -p docs/validation/load-testing
LOAD_TEST_AUTH_EMAIL="<test-account-email>" \
LOAD_TEST_AUTH_PASSWORD="<test-account-password>" \
LOAD_TEST_SCHEDULE_DATE="2026-09-18" \
uvx locust \
  -f load_tests/schedule_smoke.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 5 \
  -r 1 \
  -t 3m \
  --csv docs/validation/load-testing/issue-743-schedule-smoke-local
```

Use only synthetic accounts with existing schedule fixture data. This scenario is read-only and does not create, update, cancel, or check in schedules.

## Track C Support Read Smoke Command

`load_tests/track_c_support_smoke.py` is the #743 1차-4 Track C support read smoke scenario. It exercises a read-only authenticated support flow:

1. `POST /api/v1/auth/login`
2. `GET /api/v1/barrier-responses/{barrier_response_id}/supports`
3. Optional `GET /api/v1/support-action-plans/{support_action_plan_id}`
4. Optional `GET /api/v1/support-action-plans/{support_action_plan_id}/resources`
5. Optional `GET /api/v1/support-action-plans/{support_action_plan_id}/followups`

```bash
mkdir -p docs/validation/load-testing
LOAD_TEST_AUTH_EMAIL="<test-account-email>" \
LOAD_TEST_AUTH_PASSWORD="<test-account-password>" \
LOAD_TEST_TRACK_C_BARRIER_RESPONSE_ID="<synthetic-barrier-response-id>" \
uvx locust \
  -f load_tests/track_c_support_smoke.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 5 \
  -r 1 \
  -t 3m \
  --csv docs/validation/load-testing/issue-743-track-c-support-smoke-local
```

Use only synthetic accounts with existing Track C barrier fixture data. This scenario is read-only and does not create safety assessments, barrier responses, support action plans, or followups.

Track C support-specific reproducibility notes:

- `LOAD_TEST_TRACK_C_BARRIER_RESPONSE_ID` is required and must belong to the synthetic test account.
- `LOAD_TEST_TRACK_C_TRAVEL_SITUATION` and `LOAD_TEST_TRACK_C_SUBREASON_CODE` can be set to pin the support filter used by the target fixture.
- `LOAD_TEST_TRACK_C_SUPPORT_PLAN_ID` is optional. When omitted, the scenario measures support-offer reads only; when set, it also reads the plan, resources, and followup state.

Schedule-specific reproducibility notes:

- If `LOAD_TEST_SCHEDULE_DATE` is omitted, the scenario uses today's local date in `Asia/Seoul`; set it explicitly for evidence runs so repeated runs target the same fixture date.
- `LOAD_TEST_SCHEDULE_DETAIL_LIMIT=0` means list-only execution: the occurrence list is fetched, and medication detail requests are intentionally skipped.
- If the occurrence list is empty, the scenario records only the list request and skips detail requests; this is a valid empty-fixture smoke result, not a detail endpoint measurement.

## OCR / Worker Readiness Preflight

For OCR / Worker smoke, prefer the isolated real-stack E2E compose file before recording baseline evidence. It uses a clean tmpfs database and separate port `18000`, so stale local development volumes do not hide Worker or Alembic readiness problems.

```bash
docker compose --env-file envs/.local.env \
  -f docker-compose.real-stack-e2e.yml \
  up -d postgres redis migrate fastapi ai-worker

curl http://127.0.0.1:18000/api/openapi.json
```

First run a short 1-user preflight to confirm that Redis, Worker, OCR consent setup, storage, and synthetic authentication are all connected. Use the 5-user / 3-minute run only after this preflight succeeds.

```bash
LOAD_TEST_BEARER_TOKEN=<redacted> \
LOAD_TEST_OCR_MAX_WAIT_SECONDS=90 \
uvx locust \
  -f load_tests/ocr_worker_smoke.py \
  --host http://127.0.0.1:18000 \
  --headless \
  -u 1 \
  -r 1 \
  -t 30s \
  --csv /tmp/issue-627-ocr-worker-smoke-preflight
```

If the default local compose stack fails before Worker startup because an old development DB volume has an Alembic revision without matching tables, do not treat that as an OCR smoke failure. Rebuild/recreate the local DB deliberately, or use this isolated real-stack preflight for #627 evidence.

## OCR / Worker Smoke Command

This scenario covers the #627 1차-2 OCR / Worker minimum flow with the approved synthetic one-cycle fixture:

1. `POST /api/v1/documents`
2. `POST /api/v1/documents/{document_id}/ocr-jobs`
3. `GET /api/v1/jobs/{job_id}` polling
4. `GET /api/v1/ocr-jobs/{ocr_job_id}`
5. `PATCH /api/v1/extracted-fields/{field_id}`
6. `POST /api/v1/documents/{document_id}/prescription`
7. `GET /api/v1/prescriptions/{prescription_id}`

Prerequisites: Backend, PostgreSQL, Redis, storage, Outbox Publisher/OCR Worker, a synthetic user, required OCR consent/profile setup, and a valid access token. Record whether the run uses a real OCR Provider or an approved test double.

```bash
mkdir -p docs/validation/load-testing
LOAD_TEST_BEARER_TOKEN=<redacted> \
uvx locust \
  -f load_tests/ocr_worker_smoke.py \
  --host http://127.0.0.1:8000 \
  --headless \
  -u 5 \
  -r 1 \
  -t 3m \
  --csv docs/validation/load-testing/issue-627-ocr-worker-smoke-local
```

Do not commit the bearer token or generated CSV files. Commit only a redacted summary with request count, failure count, p50, p95, max latency, environment category, commit SHA, and unresolved limitations.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOAD_TEST_SMOKE_PATH` | `/api/openapi.json` | Absolute path used by the smoke task |
| `LOAD_TEST_EXPECT_STATUS` | `200` | Expected HTTP status for the smoke task |
| `LOAD_TEST_BEARER_TOKEN` | empty | Optional bearer token. Required for `ocr_worker_smoke.py`. Do not print or commit the value. |
| `LOAD_TEST_AUTH_EMAIL` | empty | Required by `auth_smoke.py`. Test account email; do not commit real values. |
| `LOAD_TEST_AUTH_PASSWORD` | empty | Required by `auth_smoke.py`. Test account password; do not print or commit. |
| `LOAD_TEST_AUTH_INCLUDE_REFRESH` | `false` | Optional token refresh task. Use only for single-user smoke or independent per-user accounts. |
| `LOAD_TEST_AUTH_LOGOUT_ON_STOP` | `false` | Optional logout call on Locust user shutdown. Use carefully with shared accounts. |
| `LOAD_TEST_SCHEDULE_DATE` | today in Asia/Seoul | Local date used by `schedule_smoke.py`; set explicitly for evidence runs. |
| `LOAD_TEST_SCHEDULE_DETAIL_LIMIT` | `3` | Maximum occurrence medication detail requests per task. |
| `LOAD_TEST_TRACK_C_BARRIER_RESPONSE_ID` | empty | Required by `track_c_support_smoke.py`; synthetic barrier response fixture id. |
| `LOAD_TEST_TRACK_C_SUPPORT_PLAN_ID` | empty | Optional action plan fixture id for plan/resources/followup read checks. |
| `LOAD_TEST_TRACK_C_TRAVEL_SITUATION` | empty | Optional support-offer filter; allowed values are `SCHEDULE_CHANGED` and `MEDICATION_NOT_WITH_ME`. |
| `LOAD_TEST_TRACK_C_SUBREASON_CODE` | empty | Optional support-offer subreason filter for the target fixture. |
| `LOAD_TEST_OCR_MANIFEST_PATH` | `backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json` | Synthetic OCR scenario manifest |
| `LOAD_TEST_OCR_FIXTURE_PATH` | manifest `fixture_path` | Synthetic prescription image fixture |
| `LOAD_TEST_OCR_POLL_INTERVAL_SECONDS` | `1.0` | Fallback Job polling interval when `retry_after_seconds` is absent |
| `LOAD_TEST_OCR_MAX_WAIT_SECONDS` | `60.0` | Maximum wait for OCR Job completion |
| `LOAD_TEST_OCR_IDEMPOTENCY_PREFIX` | `load-test-ocr` | Prefix for generated OCR intake idempotency keys |

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
2. medication schedule read smoke: initial smoke is `load_tests/schedule_smoke.py`
3. check-in and Track C support smoke: initial support read smoke is `load_tests/track_c_support_smoke.py`
4. prescription upload/OCR polling: initial smoke is `load_tests/ocr_worker_smoke.py`
5. OCR review and prescription confirmation: initial smoke is `load_tests/ocr_worker_smoke.py`
6. Guide generation
7. Chat session and message flow
8. notifications or Web Push if enabled for the target environment

Each scenario PR should document required fixture setup, authentication state, expected status codes, and whether it is local-only, staging, or production-approved.

