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

- `load_tests/locustfile.py` with a configurable smoke task.
- Default smoke target `/api/openapi.json`, matching the deployment runbook's FastAPI HTTP liveness check.
- `LOAD_TEST_SMOKE_PATH`, `LOAD_TEST_EXPECT_STATUS`, and optional `LOAD_TEST_BEARER_TOKEN` environment variables.
- `scripts/load_testing/validate_load_test_assets.py` and a regression test to keep the framework files aligned.
- This runbook and links from the main testing/deployment documentation.

Excluded now:

- Login/signup/OCR/Guide/Chat full scenario implementation.
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

1. Auth/profile read and update.
2. Prescription upload and OCR polling using synthetic files only.
3. OCR review and prescription confirmation.
4. Guide generation.
5. Chat session creation and message send.
6. Notifications or Web Push only when the target environment has that feature enabled.

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
