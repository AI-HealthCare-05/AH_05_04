# Repository Agent Guide

## Purpose and Sources of Truth

This file gives coding agents the minimum repository-wide collaboration rules. It does not replace the team's existing documentation.

- Follow `CONTRIBUTING.md` and the GitHub Ruleset for branches, commits, reviews, pull requests, and completion checks.
- CODEOWNERS is not used. Every Issue and Pull Request names the implementation owner and responsible reviewer separately, and merge is allowed only after the responsible reviewer approves.
- Follow `SECURITY.md` and `docs/privacy-safety.md` for security, patient data, and medical safety requirements.
- Follow the relevant files under `docs/`, especially `docs/contracts/`, `docs/api.md`, `docs/data-schema.md`, and `docs/testing.md`, for architecture, contracts, schemas, and verification.

If these sources disagree or do not define a boundary clearly, do not invent a rule. Call out the ambiguity and ask the relevant owners before changing the affected area.

### Document Status and Authority

- **Current runtime contract** means behavior supported together by merged code, migrations, the current OpenAPI/Pydantic DTOs, and automated tests.
- **Approved target / Not implemented** documents are approved implementation targets, not current API, database, or deployment behavior. Approved Contract Freeze v4 is the current normative target baseline, but document approval alone does not prove implementation, tests, external approval, or public release.
- Product and planning documents provide intent and context but do not replace runtime contracts.
- Use `docs/governance/post-mvp-1-document-authority.md` for Post-MVP-1 provenance and status interpretation. If current implementation and a target differ, do not combine or infer values; reconcile the source Decision and repository target with the relevant domain reviewers.
- Changing an enum, API route or DTO, required field, error code, transaction order, or publication condition requires a new Decision or Contract Freeze version and matching contract and test updates.
- Use the status directories as the source of truth for contract status. Promote a target or proposed contract into `docs/contracts/current/` only in the implementation PR that includes the required implementation, migrations, OpenAPI/DTO, automated tests, evidence, and designated reviewer approval; update its status and index entry in the same PR, and do not leave a duplicate in the previous status directory.
- Keep Track C and Track F publication and the common Privacy Production gate closed until `docs/release-gates/post-mvp-1-external-approvals.md` is satisfied. OTC is part of Track F and has no separate Track D publication flag.

## Team Roles

The current Post-MVP-1 responsibility baseline is:

- 권가빈 — PM, product acceptance, Privacy policy and consent scope, external-approval tracking, Track C final responsibility
- 송은영 — Backend, data and Security technical controls; Track A API·DB·Outbox, Track B Backend, Track F Chat data boundary
- 김지혜 — Worker and OCR; Track A Consumer·Worker, Track C Backend, Track E delivery
- 정현우 — AI/RAG implementation owner; Track F Guide·Chat·Citation·Safety·OTC delivery and evidence
- 남한솔 — Frontend and consent UX; common Job states and Track B·C·E·F integration

Former Track D is not a separate execution or ownership unit. Its historical document and requirement IDs remain for traceability, while current OTC scope is owned and accepted under Track F.

## Reviewer Assignment and Merge

- The Issue and Pull Request name the implementation owner and responsible reviewer separately.
- Select the responsible reviewer for the actual change scope. Do not infer the reviewer from file history, document authorship, a former CODEOWNERS entry, or an unrecorded GitHub handle/name mapping.
- The PR author cannot count self-approval as the required approval.
- Merge only after the named responsible reviewer approves and blocking review comments are resolved.
- Cross-domain changes name reviewers for every affected domain. Security·Privacy, medical safety, Source and AI/RAG changes also attach the required specialist or external approval evidence when applicable.
- AI/RAG detailed assignments and PR reviewers are recorded in the 2026-08-28 responsibility matrix under 정현우's overall responsibility.

## Ownership Boundaries

- Identify the affected domains and name their responsible reviewers before editing shared contracts.
- Do not modify another owner's implementation merely because it is adjacent or convenient. Cross-owner changes require explicit task scope or prior coordination, and the relevant owners must review them.
- Keep changes within the issue's stated scope. Do not include unrelated refactors, formatting sweeps, generated files, or dependency changes.
- Co-owned and cross-cutting areas require coordination with all affected domains; ownership of one implementation area does not grant authority to change a shared interface unilaterally.

## Shared Contracts and Implementations

A shared contract is an externally consumed API, request or response shape, error or status meaning, database or message schema, or other interface shared across Frontend, Backend, OCR, RAG, LLM, or Evaluation. An implementation change stays behind an existing contract and does not alter those observable semantics.

- Do not disguise a contract change as an implementation detail. Removing or renaming fields, changing types or meanings, adding required fields, or changing shared states is a contract change.
- Before implementation and during pull request review, explicitly determine whether the change affects a shared API, data structure, error meaning, state transition, or cross-domain DTO.
- Coordinate a proposed contract change with every affected owner before implementation. Update the authoritative contract or schema, affected implementations, documentation, and contract or integration tests together in the same focused pull request.
- For every shared-contract change, update the relevant Markdown file under `docs/contracts/current/`, `docs/contracts/targets/`, or `docs/contracts/proposed/` according to its status and add it to `docs/contracts/README.md`. Do not duplicate a contract across status folders. Promote a target into `current/` only in the implementation PR that includes the required code, migrations, OpenAPI/DTO, automated tests, evidence, and designated reviewer approval. The absence of an existing file is not a reason to leave the contract undocumented.
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
