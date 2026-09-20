"""Exact read-only SQLAlchemy adapter for Evaluation Guard authority (#162).

This adapter provides an exact read-only boundary for:
1. Evaluation-start Candidate Runtime Authority (`read_candidate_start`):
   - Exact query joining `rag_runtime_release_bundle`, `rag_runtime_execution_manifest`,
     and `rag_runtime_environment`.
   - Reconstructs and cryptographically recomputes Execution Manifest hash from table fields.
   - Reconstructs and cryptographically recomputes Runtime Bundle hash from table fields,
     including bundle sources and citation approval pins.
   - Strictly enforces BUILDING status, LOCAL environment, and governance revision match.
   - Never uses active pointer columns (`active_bundle_id`, `active_bundle_manifest_hash`).
2. Run-finalization Environment Concurrency Fence (`read_environment_fence`):
   - Exact re-read of `rag_runtime_environment` for `environment_revision`,
     `governance_revision_ref`, and `safety_epoch`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Integer, String, and_, column, select, table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberIdentity,
    RuntimeBundleCanonicalConfiguration,
    RuntimeBundleCitationApprovalPinIdentity,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberIdentity,
    RuntimeExecutionManifestInput,
    canonical_execution_manifest_hash,
    canonical_runtime_bundle_manifest_hash,
)
from rag_runtime.source_use_approval import SourceUsePurpose

SessionFactory = Callable[[], AsyncSession]

_BUNDLE = table(
    "rag_runtime_release_bundle",
    column("id", String(36)),
    column("bundle_key", String(120)),
    column("bundle_version", String(80)),
    column("bundle_status", String(20)),
    column("execution_manifest_id", String(36)),
    column("bundle_manifest_hash", String(64)),
    column("environment_code", String(50)),
    column("catalog_version", String(80)),
    column("catalog_manifest_hash", String(64)),
    column("candidate_index_ref", String(255)),
    column("candidate_index_version", String(80)),
    column("candidate_index_manifest_hash", String(64)),
    column("knowledge_index_ref", String(255)),
    column("knowledge_index_version", String(80)),
    column("knowledge_index_manifest_hash", String(64)),
    column("rule_set_ref", String(255)),
    column("rule_set_version", String(80)),
    column("guideline_set_ref", String(255)),
    column("guideline_set_version", String(80)),
    column("safety_policy_ref", String(255)),
    column("safety_policy_version", String(80)),
    column("governance_revision_ref", String(255)),
)

_MANIFEST = table(
    "rag_runtime_execution_manifest",
    column("id", String(36)),
    column("manifest_key", String(120)),
    column("manifest_version", String(80)),
    column("manifest_hash", String(64)),
    column("schema_version", String(80)),
    column("git_commit_sha", String(40)),
    column("worker_artifact_ref", String(255)),
    column("model_ref", String(255)),
    column("prompt_ref", String(255)),
    column("parser_ref", String(255)),
    column("resolver_ref", String(255)),
    column("guard_policy_ref", String(255)),
)

_BUNDLE_SOURCE = table(
    "rag_runtime_bundle_source",
    column("id", String(36)),
    column("bundle_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(255)),
    column("canonical_checksum", String(64)),
    column("approval_version", String(80)),
    column("scope_policy_hash", String(64)),
    column("freshness_policy_hash", String(64)),
    column("source_purpose", String(30)),
    column("required", Boolean),
    column("selected_for_operation", Boolean),
)

_PIN = table(
    "rag_runtime_bundle_citation_approval",
    column("id", String(36)),
    column("bundle_id", String(36)),
    column("bundle_manifest_hash", String(64)),
    column("source_snapshot_id", String(36)),
    column("source_use_approval_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("approval_version", String(120)),
    column("environment", String(20)),
    column("purpose", String(40)),
)

_ENVIRONMENT = table(
    "rag_runtime_environment",
    column("id", String(36)),
    column("environment_code", String(50)),
    column("environment_status", String(20)),
    column("environment_revision", Integer),
    column("governance_revision_ref", String(255)),
    column("safety_epoch", Integer),
)

_ARTIFACT_COLUMN_PREFIX = {
    "candidate_index": RuntimeBundleArtifactKind.CANDIDATE_INDEX,
    "knowledge_index": RuntimeBundleArtifactKind.KNOWLEDGE_INDEX,
    "rule_set": RuntimeBundleArtifactKind.RULE_SET,
    "guideline_set": RuntimeBundleArtifactKind.GUIDELINE_SET,
    "safety_policy": RuntimeBundleArtifactKind.SAFETY_POLICY,
}
_HASHED_PREFIXES = frozenset({"candidate_index", "knowledge_index"})


class EvaluationGuardAuthorityReadError(RuntimeError):
    """The authority store cannot be trusted or failed an exact read verification."""


@dataclass(frozen=True, slots=True)
class EvaluationRuntimeAuthorityObservation:
    """Pure observation representing exact runtime authority for evaluation start."""

    bundle_id: UUID
    bundle_manifest_hash: str
    bundle_status: str
    environment_code: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    governance_revision_ref: str
    environment_revision: int
    safety_epoch: int


@dataclass(frozen=True, slots=True)
class EvaluationEnvironmentFenceObservation:
    """Pure observation representing exact environment state for concurrency fence."""

    environment_code: str
    environment_revision: int
    governance_revision_ref: str
    safety_epoch: int


class SqlAlchemyEvaluationGuardAuthorityReader:
    """Read-only seam for evaluation guard runtime authority."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_candidate_start(
        self,
        *,
        bundle_id: UUID,
        bundle_manifest_hash: str,
    ) -> EvaluationRuntimeAuthorityObservation | None:
        """Exact read of a candidate bundle, execution manifest, and environment snapshot.

        Recomputes manifest and bundle hashes independently from persisted table rows.
        Does NOT rely on active bundle pointers.
        """
        if type(bundle_id) is not UUID or len(bundle_manifest_hash) != 64:
            raise EvaluationGuardAuthorityReadError("invalid bundle identifier or hash")

        statement = (
            select(
                _BUNDLE.c.id.label("bundle_id"),
                _BUNDLE.c.bundle_status.label("bundle_status"),
                _BUNDLE.c.execution_manifest_id.label("execution_manifest_id"),
                _BUNDLE.c.bundle_manifest_hash.label("bundle_manifest_hash"),
                _BUNDLE.c.environment_code.label("environment_code"),
                _BUNDLE.c.catalog_version.label("catalog_version"),
                _BUNDLE.c.catalog_manifest_hash.label("catalog_manifest_hash"),
                _BUNDLE.c.candidate_index_ref.label("candidate_index_ref"),
                _BUNDLE.c.candidate_index_version.label("candidate_index_version"),
                _BUNDLE.c.candidate_index_manifest_hash.label("candidate_index_manifest_hash"),
                _BUNDLE.c.knowledge_index_ref.label("knowledge_index_ref"),
                _BUNDLE.c.knowledge_index_version.label("knowledge_index_version"),
                _BUNDLE.c.knowledge_index_manifest_hash.label("knowledge_index_manifest_hash"),
                _BUNDLE.c.rule_set_ref.label("rule_set_ref"),
                _BUNDLE.c.rule_set_version.label("rule_set_version"),
                _BUNDLE.c.guideline_set_ref.label("guideline_set_ref"),
                _BUNDLE.c.guideline_set_version.label("guideline_set_version"),
                _BUNDLE.c.safety_policy_ref.label("safety_policy_ref"),
                _BUNDLE.c.safety_policy_version.label("safety_policy_version"),
                _BUNDLE.c.governance_revision_ref.label("bundle_governance_revision_ref"),
                _MANIFEST.c.id.label("manifest_id"),
                _MANIFEST.c.manifest_key.label("manifest_key"),
                _MANIFEST.c.manifest_version.label("manifest_version"),
                _MANIFEST.c.manifest_hash.label("manifest_hash"),
                _MANIFEST.c.schema_version.label("manifest_schema_version"),
                _MANIFEST.c.git_commit_sha.label("git_commit_sha"),
                _MANIFEST.c.worker_artifact_ref.label("worker_artifact_ref"),
                _MANIFEST.c.model_ref.label("model_ref"),
                _MANIFEST.c.prompt_ref.label("prompt_ref"),
                _MANIFEST.c.parser_ref.label("parser_ref"),
                _MANIFEST.c.resolver_ref.label("resolver_ref"),
                _MANIFEST.c.guard_policy_ref.label("guard_policy_ref"),
                _ENVIRONMENT.c.environment_code.label("env_environment_code"),
                _ENVIRONMENT.c.environment_revision.label("environment_revision"),
                _ENVIRONMENT.c.governance_revision_ref.label("env_governance_revision_ref"),
                _ENVIRONMENT.c.safety_epoch.label("safety_epoch"),
            )
            .select_from(
                _BUNDLE.join(
                    _MANIFEST,
                    _BUNDLE.c.execution_manifest_id == _MANIFEST.c.id,
                ).join(
                    _ENVIRONMENT,
                    _BUNDLE.c.environment_code == _ENVIRONMENT.c.environment_code,
                )
            )
            .where(
                and_(
                    _BUNDLE.c.id == str(bundle_id),
                    _BUNDLE.c.bundle_manifest_hash == bundle_manifest_hash,
                )
            )
        )

        sources_stmt = select(*_BUNDLE_SOURCE.c).where(_BUNDLE_SOURCE.c.bundle_id == str(bundle_id))
        pins_stmt = select(*_PIN.c).where(_PIN.c.bundle_id == str(bundle_id))

        try:
            async with self._session_factory() as session:
                row = (await session.execute(statement)).mappings().one_or_none()
                if row is None:
                    return None
                source_rows = list((await session.execute(sources_stmt)).mappings().all())
                pin_rows = list((await session.execute(pins_stmt)).mappings().all())
        except SQLAlchemyError as error:
            raise EvaluationGuardAuthorityReadError("evaluation guard authority read failed") from error

        # 1. Structural state validations
        (
            bundle_status,
            environment_code,
            bundle_gov_ref,
            environment_revision,
            safety_epoch,
        ) = _verify_candidate_structural_state(row)

        # 2. Cryptographic Execution Manifest recomputation
        manifest_id, recomputed_manifest_hash = _recompute_and_verify_execution_manifest_hash(row)

        # 3. Cryptographic Runtime Bundle recomputation
        _recompute_and_verify_bundle_manifest_hash(
            row=row,
            source_rows=source_rows,
            pin_rows=pin_rows,
            environment_code=environment_code,
            manifest_hash=recomputed_manifest_hash,
            expected_bundle_hash=bundle_manifest_hash,
        )

        return EvaluationRuntimeAuthorityObservation(
            bundle_id=bundle_id,
            bundle_manifest_hash=bundle_manifest_hash,
            bundle_status=bundle_status,
            environment_code=environment_code,
            runtime_execution_manifest_id=manifest_id,
            runtime_execution_manifest_hash=recomputed_manifest_hash,
            governance_revision_ref=bundle_gov_ref,
            environment_revision=environment_revision,
            safety_epoch=safety_epoch,
        )

    async def read_environment_fence(
        self,
        environment_code: str = "LOCAL",
    ) -> EvaluationEnvironmentFenceObservation | None:
        """Exact read of current environment state for run-finalization concurrency fence."""
        statement = select(
            _ENVIRONMENT.c.environment_code,
            _ENVIRONMENT.c.environment_revision,
            _ENVIRONMENT.c.governance_revision_ref,
            _ENVIRONMENT.c.safety_epoch,
        ).where(_ENVIRONMENT.c.environment_code == environment_code)

        try:
            async with self._session_factory() as session:
                row = (await session.execute(statement)).mappings().one_or_none()
                if row is None:
                    return None
        except SQLAlchemyError as error:
            raise EvaluationGuardAuthorityReadError("environment fence read failed") from error

        try:
            env_code = str(row["environment_code"])
            env_rev = int(row["environment_revision"])
            gov_ref = str(row["governance_revision_ref"])
            safety_epoch = int(row["safety_epoch"])
            if env_rev < 1 or safety_epoch < 1 or not gov_ref.strip():
                raise ValueError("invalid environment row values")
        except Exception as error:
            raise EvaluationGuardAuthorityReadError("corrupt environment row") from error

        return EvaluationEnvironmentFenceObservation(
            environment_code=env_code,
            environment_revision=env_rev,
            governance_revision_ref=gov_ref,
            safety_epoch=safety_epoch,
        )


def _verify_candidate_structural_state(row: Any) -> tuple[str, str, str, int, int]:
    bundle_status = str(row["bundle_status"])
    if bundle_status != "BUILDING":
        raise EvaluationGuardAuthorityReadError(f"bundle status is {bundle_status}, must be BUILDING")

    environment_code = str(row["environment_code"])
    if environment_code != "LOCAL":
        raise EvaluationGuardAuthorityReadError(f"environment is {environment_code}, must be LOCAL")

    bundle_gov_ref = row["bundle_governance_revision_ref"]
    if not bundle_gov_ref or not str(bundle_gov_ref).strip():
        raise EvaluationGuardAuthorityReadError("bundle governance_revision_ref is empty")
    bundle_gov_ref = str(bundle_gov_ref)

    env_gov_ref = row["env_governance_revision_ref"]
    if bundle_gov_ref != env_gov_ref:
        raise EvaluationGuardAuthorityReadError("bundle governance_revision_ref does not match environment")

    environment_revision = int(row["environment_revision"])
    if environment_revision < 1:
        raise EvaluationGuardAuthorityReadError("environment_revision must be >= 1")

    safety_epoch = int(row["safety_epoch"])
    if safety_epoch < 1:
        raise EvaluationGuardAuthorityReadError("safety_epoch must be >= 1")

    return bundle_status, environment_code, bundle_gov_ref, environment_revision, safety_epoch


def _recompute_and_verify_execution_manifest_hash(row: Any) -> tuple[UUID, str]:
    manifest_input = RuntimeExecutionManifestInput(
        manifest_key=str(row["manifest_key"]),
        manifest_version=str(row["manifest_version"]),
        schema_version=str(row["manifest_schema_version"]),
        git_commit_sha=str(row["git_commit_sha"]),
        worker_artifact_ref=str(row["worker_artifact_ref"]) if row["worker_artifact_ref"] else None,
        model_ref=str(row["model_ref"]) if row["model_ref"] else None,
        prompt_ref=str(row["prompt_ref"]) if row["prompt_ref"] else None,
        parser_ref=str(row["parser_ref"]) if row["parser_ref"] else None,
        resolver_ref=str(row["resolver_ref"]) if row["resolver_ref"] else None,
        guard_policy_ref=str(row["guard_policy_ref"]) if row["guard_policy_ref"] else None,
    )
    recomputed_manifest_hash = canonical_execution_manifest_hash(manifest_input)
    stored_manifest_hash = str(row["manifest_hash"])
    if recomputed_manifest_hash != stored_manifest_hash:
        raise EvaluationGuardAuthorityReadError("Execution Manifest fields do not match stored manifest_hash")
    return UUID(str(row["execution_manifest_id"])), recomputed_manifest_hash


def _recompute_and_verify_bundle_manifest_hash(
    *,
    row: Any,
    source_rows: Sequence[Any],
    pin_rows: Sequence[Any],
    environment_code: str,
    manifest_hash: str,
    expected_bundle_hash: str,
) -> None:
    artifact_members: list[RuntimeBundleArtifactMemberIdentity] = []
    for prefix, kind in _ARTIFACT_COLUMN_PREFIX.items():
        ref = row[f"{prefix}_ref"]
        ver = row[f"{prefix}_version"]
        if ref is None or ver is None:
            continue
        artifact_hash = (
            str(row[f"{prefix}_manifest_hash"])
            if prefix in _HASHED_PREFIXES and row[f"{prefix}_manifest_hash"] is not None
            else None
        )
        artifact_members.append(
            RuntimeBundleArtifactMemberIdentity(
                artifact_kind=kind,
                artifact_ref=str(ref),
                artifact_version=str(ver),
                manifest_hash=artifact_hash,
            )
        )

    source_members = tuple(
        RuntimeBundleSourceMemberIdentity(
            source_snapshot_id=str(s["source_snapshot_id"]),
            source_purpose=RuntimeBundleMemberPurpose(str(s["source_purpose"])),
            source_version=str(s["source_version"]),
            canonical_checksum=str(s["canonical_checksum"]),
            approval_version=str(s["approval_version"]),
            scope_policy_hash=str(s["scope_policy_hash"]),
            freshness_policy_hash=str(s["freshness_policy_hash"]),
            required=bool(s["required"]),
            selected_for_operation=bool(s["selected_for_operation"]),
        )
        for s in source_rows
    )

    citation_pins = tuple(
        RuntimeBundleCitationApprovalPinIdentity(
            source_snapshot_id=str(p["source_snapshot_id"]),
            source_use_approval_id=str(p["source_use_approval_id"]),
            source_code=str(p["source_code"]),
            source_version=str(p["source_version"]),
            approval_version=str(p["approval_version"]),
            environment=str(p["environment"]),
            purpose=SourceUsePurpose(str(p["purpose"])),
        )
        for p in pin_rows
    )

    configuration = RuntimeBundleCanonicalConfiguration(
        environment_code=environment_code,
        execution_manifest_hash=manifest_hash,
        catalog_version=str(row["catalog_version"]),
        catalog_manifest_hash=str(row["catalog_manifest_hash"]),
        source_members=source_members,
        artifact_members=tuple(artifact_members),
        citation_approval_pins=citation_pins,
    )

    recomputed_bundle_hash = canonical_runtime_bundle_manifest_hash(configuration)
    if recomputed_bundle_hash != expected_bundle_hash:
        raise EvaluationGuardAuthorityReadError("Runtime Bundle rows do not match stored bundle_manifest_hash")
