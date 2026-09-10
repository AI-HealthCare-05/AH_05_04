# Protected Retrieval Infrastructure Adapter Implementation Plan

## Status

**Superseded — do not execute.**

This plan previously described a function-only PostgreSQL boundary. Current `AGENTS.md`, `CONTRIBUTING.md`,
PD-398-R1/R2, and `scripts/ci/check_database_logic.py` prohibit the database functions required by that design.

## Replacement design

The reviewed replacement target is:

`docs/superpowers/specs/2026-09-10-protected-retrieval-infrastructure-adapter-design.md`

It moves identity, authorization, capability, operation, and audit lifecycle validation into explicit Python
Service/Repository transactions backed by ordinary constraints and exact column privileges.

## Handoff rule

Do not implement from this file. After the revised design's privilege matrix, transaction order, residual risk, and
activation gates are approved, create a new TDD implementation plan using the `writing-plans` workflow. The new plan
must replace this tombstone rather than append executable steps below it.
