# Load Testing Framework (#627)

## Status

This document records the #627 framework and operating procedure baseline. It does not prove production capacity, does not complete API별 시나리오 coverage, and does not claim `p95 <= 3s` for any user journey.

## Purpose

The goal is to prepare a repeatable load-test entrypoint before the API surface stabilizes. The framework must let the team add scenario groups later without re-deciding tool choice, result locations, or evidence boundaries.

## Tool Choice

Use Locust for the repository-owned load-test entrypoint.

Reasons:

- The project is Python-first, so future authenticated flows can reuse Python helpers and fixtures more easily than a separate JavaScript stack.
- A single `load_tests/locustfile.py` can run locally or against an approved staging/production endpoint.
- The initial smoke task can be validated without adding a permanent dependency to `pyproject.toml` by running Locust through `uvx`.

The repository does not vendor Locust or commit generated load-test output in this PR.

## Current Scope

Included now:

- `load_tests/locustfile.py` with a configurable framework smoke task.
- `load_tests/auth_smoke.py` with the #627 1차-1 Auth baseline smoke flow.
- `load_tests/ocr_worker_smoke.py` with the #627 1차-2 OCR / Worker minimum smoke flow.
- `load_tests/schedule_smoke.py` with the #743 medication schedule read smoke flow.
- Default framework smoke target `/api/openapi.json`, matching the deployment runbook's FastAPI HTTP liveness check.
- `LOAD_TEST_SMOKE_PATH`, `LOAD_TEST_EXPECT_STATUS`, Auth/OCR smoke variables, and optional `LOAD_TEST_BEARER_TOKEN` environment variables.
- `scripts/load_testing/validate_load_test_assets.py` and a regression test to keep the framework files aligned.
- This runbook and links from the main testing/deployment documentation.

Excluded now:

- Login/signup/Guide/Chat full scenario implementation.
- Schedule create/update/cancel/check-in load scenarios.
- OCR Provider capacity approval or final `p95 <= 3s` claim.
- Real patient data, real prescription files, or provider payload replay.
- Production capacity claim, SLO approval, or Privacy/Release gate approval.
- Dashboard, alerting, autoscaling, or infrastructure changes.

## Local Framework Smoke

Start the local Backend first, then run:

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

For an approved staging or production-like endpoint, replace `--host` with the public base URL category approved for the run. Do not paste tokens, cookies, or secret-bearing URLs into GitHub, Discord, PR comments, or committed docs.

## Auth Baseline Smoke

`load_tests/auth_smoke.py` is the first API-specific baseline scenario for #627. It runs the minimum reusable authenticated flow:

1. `POST /api/v1/auth/login`
2. `GET /api/v1/users/me`

Run it with a synthetic test account only:

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

This is a baseline/smoke load run. It records p50/p95/max latency and failure count, but it is not a final production `p95 <= 3s` approval. Token refresh and logout are disabled by default because they rotate or invalidate token state; enable `LOAD_TEST_AUTH_INCLUDE_REFRESH=true` or `LOAD_TEST_AUTH_LOGOUT_ON_STOP=true` only for a single-user smoke or independent per-user test accounts.

Optional refresh/logout smoke should use a single Locust user unless each Locust user has an independent account:

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

## Medication Schedule Read Smoke

`load_tests/schedule_smoke.py` is the #743 1차-3 read-only schedule scenario. It uses the same synthetic-account principle as the other smoke scenarios and covers:

1. `POST /api/v1/auth/login`
2. `GET /api/v1/medication-occurrences?date=YYYY-MM-DD`
3. `GET /api/v1/medication-occurrences/{occurrence_id}/medication` for a bounded subset

Run it only with a synthetic account that already has medication schedule fixture data:

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

This scenario is read-only. It does not create schedules, edit schedule times, cancel occurrences, or submit check-ins. Treat it as baseline/smoke evidence, not as a production capacity claim.

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

## OCR / Worker Minimum Smoke

`load_tests/ocr_worker_smoke.py` is the first API-specific scenario for #627. It uses only the approved synthetic release-validation fixture and follows the current Backend contract:

1. Upload synthetic prescription document.
2. Create OCR Job with `Idempotency-Key`.
3. Poll common Job status until `COMPLETED`.
4. Read OCR domain result.
5. Review extracted fields with manifest values.
6. Confirm prescription.
7. Re-read the confirmed prescription.

Run it only after Backend, PostgreSQL, Redis, storage, Outbox Publisher/OCR Worker, required consent/profile setup, and a synthetic access token are ready. Record whether the run used a real OCR Provider or an approved test double.

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

This is baseline/smoke evidence. It confirms that the OCR / Worker flow can complete repeatedly under a low load and records p50/p95/max latency. It is not a Production capacity claim, not a final `p95 <= 3s` assertion, and not Privacy/Release approval for real prescription traffic.

## Result Summary Template

Use this structure when recording a non-sensitive summary:

```markdown
- Issue/PR:
- Commit SHA:
- Target environment: local | staging | production-approved
- Base URL category: localhost | CloudFront | internal staging
- Command: secrets redacted
- Users / spawn rate / duration:
- Scenario file:
- Scenario list:
- Excluded scenarios:
- Total requests:
- Failures:
- p50 / p95 / max latency:
- Error notes:
- Remaining limitations:
```

Full CSV output can be attached only if it contains no tokens, cookies, patient data, prescription text, provider payloads, or internal endpoints that should remain private. Otherwise store it in an approved private evidence location and commit only the summary.

## Future Scenario Order

After API contracts settle, add scenarios in focused follow-up PRs:

1. User/profile read smoke after the Auth baseline.
2. Medication schedule read smoke. Initial scenario: `load_tests/schedule_smoke.py`.
3. Check-in and Track C support smoke.
4. Prescription upload and OCR polling using synthetic files only. Initial scenario: `load_tests/ocr_worker_smoke.py`.
5. OCR review and prescription confirmation. Initial scenario: `load_tests/ocr_worker_smoke.py`.
6. Guide generation.
7. Chat session creation and message send.
8. Notifications or Web Push only when the target environment has that feature enabled.

Each scenario should define setup data, authentication method, expected status codes, cleanup expectations, and whether it is local-only or approved for staging/production.

## Security Boundary

- Never commit bearer tokens, cookies, `.env` values, API keys, provider payloads, patient data, prescription images, OCR raw text, or full server logs.
- Use only synthetic or approved non-sensitive fixture data.
- Treat load-test evidence as operational evidence, not as Privacy Production approval.
- Keep `/api/openapi.json` as the default framework smoke path. The production runbook explicitly says `/api/v1/health` is not the app liveness endpoint.

## Validation

Repository-side validation for this baseline:

```bash
uv run python scripts/load_testing/validate_load_test_assets.py
uv run pytest tests/services/test_load_test_assets.py -q
```

A real Locust run requires the target service to be running and may use `uvx locust`. The framework PR does not require committing generated CSV files.

