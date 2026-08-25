# Repository Agent Guide

## Purpose and Sources of Truth

This file gives coding agents the minimum repository-wide collaboration rules. It does not replace the team's existing documentation.

- Follow `CONTRIBUTING.md` and the GitHub Ruleset for branches, commits, reviews, pull requests, and completion checks.
- Treat the review routing table in this file as the source of truth for path ownership and manually requested reviewers.
- Follow `SECURITY.md` and `docs/privacy-safety.md` for security, patient data, and medical safety requirements.
- Follow the relevant files under `docs/`, especially `docs/contracts/`, `docs/api.md`, `docs/data-schema.md`, and `docs/testing.md`, for architecture, contracts, schemas, and verification.

If these sources disagree or do not define a boundary clearly, do not invent a rule. Call out the ambiguity and ask the relevant owners before changing the affected area.

### Document Status and Authority

- **Current runtime contract** means behavior supported together by merged code, migrations, the current OpenAPI/Pydantic DTOs, and automated tests.
- **Approved target / Not implemented** documents are approved implementation targets, not current API, database, or deployment behavior. Document approval alone does not prove implementation, tests, external approval, or public release.
- Product and planning documents provide intent and context but do not replace runtime contracts.
- Use `docs/governance/post-mvp-1-document-authority.md` for Post-MVP-1 provenance and status interpretation. If current implementation and a target differ, do not combine or infer values; reconcile the source Decision and repository target with the relevant domain reviewers.
- Changing an enum, API route or DTO, required field, error code, transaction order, or publication condition requires a new Decision or Contract Freeze version and matching contract and test updates.
- Do not move a target contract into a new folder to promote it. Keep the path stable and update its status only after implementation, migrations, OpenAPI, automated tests, and reviewer approval evidence are linked.
- Keep Track C, D, and F publication and the common Privacy Production gate closed until `docs/release-gates/post-mvp-1-external-approvals.md` is satisfied.

## Team Roles

The current team roles recorded in Issue #9 are:

- 권가빈 — Product / Architecture / AI Integration / 전체 일정
- 남한솔 — Frontend / UX
- 송은영 — Backend / DB / API
- 김지혜 — OCR / 의료정보 구조화
- 정현우 — RAG / LLM / Evaluation

The repository does not explicitly map these names to GitHub handles. Do not infer that mapping. Manually request review from the following handles according to the changed paths:

- Repository default: `*` — `@ceohwj`
- Product, architecture, and repository operations:
  - `/.github/` — `@ceohwj`, `@hazelnutflavoured`
  - `/docs/architecture.md` — `@hazelnutflavoured`, `@ceohwj`
  - `/docs/contracts/` — `@hazelnutflavoured`, `@phina-io`, `@ceohwj`
- Frontend / UX: `/frontend/` — `@solia142`
- Backend / DB / API:
  - `/backend/app/` — `@phina-io`
  - `/docs/api.md` — `@phina-io`, `@hazelnutflavoured`
  - `/docs/data-schema.md` — `@phina-io`, `@hazelnutflavoured`
- OCR and medical information structuring:
  - `/ai_worker/tasks/ocr/` — `@Jye-rookie`
  - `/ai_worker/tests/ocr/` — `@Jye-rookie`
- RAG, LLM, and evaluation:
  - `/ai_worker/tasks/rag/` — `@ceohwj`
  - `/ai_worker/tasks/llm/` — `@ceohwj`
  - `/ai_worker/tasks/evaluation/` — `@ceohwj`
  - `/ai_worker/tests/rag/` — `@ceohwj`
  - `/ai_worker/tests/llm/` — `@ceohwj`
  - `/ai_worker/tests/evaluation/` — `@ceohwj`
  - `/evals/` — `@ceohwj`, `@Jye-rookie`
  - `/knowledge/` — `@ceohwj`

## Ownership Boundaries

- Identify the affected path and its designated reviewers before editing.
- Do not modify another owner's implementation merely because it is adjacent or convenient. Cross-owner changes require explicit task scope or prior coordination, and the relevant owners must review them.
- Keep changes within the issue's stated scope. Do not include unrelated refactors, formatting sweeps, generated files, or dependency changes.
- Co-owned and cross-cutting areas require coordination with all affected domains; ownership of one implementation area does not grant authority to change a shared interface unilaterally.

## Shared Contracts and Implementations

A shared contract is an externally consumed API, request or response shape, error or status meaning, database or message schema, or other interface shared across Frontend, Backend, OCR, RAG, LLM, or Evaluation. An implementation change stays behind an existing contract and does not alter those observable semantics.

- Do not disguise a contract change as an implementation detail. Removing or renaming fields, changing types or meanings, adding required fields, or changing shared states is a contract change.
- Before implementation and during pull request review, explicitly determine whether the change affects a shared API, data structure, error meaning, state transition, or cross-domain DTO.
- Coordinate a proposed contract change with every affected owner before implementation. Update the authoritative contract or schema, affected implementations, documentation, and contract or integration tests together in the same focused pull request.
- For every shared-contract change, update the relevant Markdown file under `docs/contracts/`. If no relevant contract file exists, create a focused `docs/contracts/<contract-name>.md` and add it to `docs/contracts/README.md`; the absence of an existing file is not a reason to leave the contract undocumented.
- Pull request reviewers must treat a missing or stale `docs/contracts/` document, missing index entry, or missing contract/integration coverage as a blocking finding when shared behavior changed. Verify that the contract document, authoritative API or schema documentation, implementation, and tests describe the same fields, types, requiredness, error semantics, and states.
- When implementing an already agreed contract without changing it, stay within the assigned ownership boundary and add or update the relevant local tests.

## Safety

- Never commit secrets or credentials, including `.env` files, API keys, tokens, passwords, and certificates.
- Never add real patient information, prescriptions, medical records, or re-identifiable data. Repository examples and test fixtures must use approved de-identified synthetic data.
- If a change could expose sensitive data or weaken medical safety controls, stop and follow `SECURITY.md` and `docs/privacy-safety.md`; do not improvise a workaround.

## Verification and Pull Request Scope

- Run the smallest relevant checks first, then the required checks defined in `CONTRIBUTING.md` and `docs/testing.md` for the affected area.
- Shared-contract changes require the relevant contract and integration tests. Medical AI changes require the applicable `evals/` checks.
- For documentation-only changes, review the rendered content, references, repository scope, `git diff --check`, and the complete `git diff`.
- Keep each pull request small, issue-focused, and easy for the designated reviewers to review. Clearly report checks that were run, skipped, or could not run.
