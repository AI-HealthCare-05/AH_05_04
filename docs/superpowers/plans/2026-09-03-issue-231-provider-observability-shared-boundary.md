# Issue #231 Provider Observability Shared Boundary Implementation Plan

> **For agentic workers:** Follow this plan sequentially and keep every Provider-facing test mock-based.

**Goal:** Make Backend and AI Worker share one import-neutral Provider observability data contract without pulling Backend configuration or DB credentials into Worker imports.

**Architecture:** Add a root `provider_contracts` package containing only enums and validated frozen dataclasses. Backend aliases its existing `Env` name and re-exports the shared Provider types while retaining all logger/runtime behavior. Worker requires an explicit environment and creates its Provider context from a validated `WorkerMessage`; Docker images copy the shared package explicitly.

**Tech Stack:** Python 3.13, dataclasses, StrEnum, UUID, Pydantic v2, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-03-issue-231-provider-observability-shared-boundary-design.md`

## Global constraints

- Do not change public API, DB schema, Stream message schema, Provider payloads, timeouts, retry policy, or error semantics.
- Do not implement Worker Provider adapter or OCR Handler wiring; that remains Issue #232.
- Do not add a Worker observability opt-out or duplicate Provider contract types.
- Do not change staging or production deployment configuration.
- Do not include credentials, Provider payloads, OCR text, or real medical data in code or test output.
- Do not execute real CLOVA or OpenAI calls.
- Add each behavioral test first, run it, and observe the expected failure before production code changes.

---

### Task 1: Lock the import-neutral shared contract

**Files:**

- Create: `tests/contract/test_provider_observability_shared_boundary.py`
- Create: `provider_contracts/__init__.py`
- Create: `provider_contracts/observability.py`

**Steps:**

1. Add tests for every accepted and rejected `ProviderCallContext` and `ProviderCallDescriptor` invariant currently owned by Backend.
2. Add a subprocess test that removes Backend DB variables, imports `provider_contracts.observability`, creates valid objects, and proves neither `app.core` nor `ai_worker.core` was imported.
3. Run the new contract test and confirm collection fails because `provider_contracts` does not exist.
4. Move the enums and frozen data contracts into the new package without changing values, validation branches, or safe error messages.
5. Re-run the contract test and confirm it passes.

### Task 2: Preserve Backend compatibility and behavior

**Files:**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/provider_observability.py`
- Modify: `backend/app/tests/test_config.py`
- Modify: `backend/app/tests/core/test_provider_observability.py`

**Steps:**

1. Add failing identity assertions proving `app.core.config.Env` is the shared `DeploymentEnvironment` object and Backend Provider imports are the shared objects.
2. Run the focused Backend config and Provider observability tests and observe the new identity assertions fail.
3. Replace the Backend `Env` definition with a shared enum alias and replace local Provider enum/dataclass definitions with imports from the shared package.
4. Leave `ProviderCallLogger`, `ProviderCallSpan`, `ProviderCallObserver`, the 20-field allowlist, identifier exposure guard, and terminal-event logic unchanged.
5. Re-run focused Backend tests, including adapter and dependency-wiring observability coverage.

### Task 3: Require Worker environment and construct a safe context

**Files:**

- Create: `ai_worker/core/provider_observability.py`
- Create: `ai_worker/tests/core/test_provider_observability.py`
- Modify: `ai_worker/core/config.py`
- Modify: `ai_worker/tests/core/test_config.py`

**Steps:**

1. Add failing Worker tests for parsing `local`, `staging`, and `production`; rejecting a missing or invalid `ENV`; and returning the shared enum type.
2. Add failing factory tests proving the result preserves the validated message trace and environment while fixing `validation_run_id=None` and `validation_enabled=False`.
3. Run the focused Worker tests and confirm failure because `ENV` and the factory are absent.
4. Add required `Config.ENV: DeploymentEnvironment` with no default. Update unrelated Worker config tests to pass explicit `ENV="local"` rather than weakening the requirement.
5. Implement the pure context factory without importing Backend code, configuration objects, logging, or Provider adapters.
6. Re-run the Worker tests and confirm they pass.

### Task 4: Include the shared package in both images

**Files:**

- Modify: `backend/app/Dockerfile`
- Modify: `ai_worker/Dockerfile`
- Create: `tests/contract/test_provider_contracts_docker_copy.py`

**Steps:**

1. Add a focused contract test proving each Dockerfile explicitly copies `provider_contracts` into `/app/provider_contracts`.
2. Run it and observe failure against both current Dockerfiles.
3. Add the same explicit `COPY ./provider_contracts ./provider_contracts` instruction to both images without changing dependencies, credentials, commands, or deployment environment values.
4. Re-run the Docker contract test. Do not perform networked image builds unless existing local verification requires them.

### Task 5: Integrated mock and static verification

**Files:** all changed files

**Steps:**

1. Run the new contract, Backend observability/config, Worker config/context, and Docker contract tests.
2. Run the repository test command from `CONTRIBUTING.md` with the isolated local test database.
3. Run `uv run ruff check .`.
4. Run `uv run ruff format . --check`.
5. Run `uv run mypy backend/app ai_worker provider_contracts`.
6. Run `git diff --check`, inspect the complete branch diff, and scan it for secrets, patient data, placeholders, unrelated changes, and #232 scope leakage.
7. Confirm no actual Provider Live call ran and report any unavailable verification separately.
