"""RAG-12A Runtime Bundle build execution boundary (Issue #175).

This is the internal build port: it runs the kernel judgment, converts *that same* judged input
into storage rows, and persists them atomically.  A rejected request never reaches persistence,
and a stored bundle can always be re-verified because :func:`verify_persisted_bundle_manifest_hash`
rebuilds the canonical configuration from rows and recomputes the hash.

Boundary note for review: this module is the first production import of ``ai_worker`` from
``backend``.  The alternative -- an ``ai_worker/adapters`` Protocol + adapter, as
``sqlalchemy_source_snapshot_repository.py`` does -- would have to redeclare six bundle tables as
SQLAlchemy Core ``table()`` literals, duplicating a schema whose models, migrations and reviewer
all live in ``backend``.  The dependency added here is one pure kernel module with no I/O, no
clock and no session, which is the same shape as ``backend``'s existing import of
``provider_contracts``.  The reverse direction stays forbidden and the Worker test lane still
enforces it.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberIdentity,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildOutcome,
    RuntimeBundleBuildRequest,
    RuntimeBundleCanonicalConfiguration,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberIdentity,
    canonical_runtime_bundle_manifest_hash,
    evaluate_runtime_bundle_build,
)
from app.models.rag_runtime import RagRuntimeSourcePurpose
from app.repositories.rag_runtime_repository import (
    RagRuntimeBundleBuildResult,
    RagRuntimeBundleSourceCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
    recomputed_execution_manifest_hash,
)

_ARTIFACT_COLUMN_PREFIX = {
    RuntimeBundleArtifactKind.CANDIDATE_INDEX: "candidate_index",
    RuntimeBundleArtifactKind.KNOWLEDGE_INDEX: "knowledge_index",
    RuntimeBundleArtifactKind.RULE_SET: "rule_set",
    RuntimeBundleArtifactKind.GUIDELINE_SET: "guideline_set",
    RuntimeBundleArtifactKind.SAFETY_POLICY: "safety_policy",
}
_ARTIFACT_KIND_BY_PREFIX = {prefix: kind for kind, prefix in _ARTIFACT_COLUMN_PREFIX.items()}
_HASHED_PREFIXES = frozenset({"candidate_index", "knowledge_index"})


@dataclass(frozen=True, slots=True)
class RuntimeBundleBuildExecution:
    """What the port did: the kernel judgment, and the persisted rows when it was BUILDABLE."""

    outcome: RuntimeBundleBuildOutcome
    persisted: RagRuntimeBundleBuildResult | None

    @property
    def stored(self) -> bool:
        return self.persisted is not None


async def execute_runtime_bundle_build(
    session: AsyncSession,
    request: RuntimeBundleBuildRequest,
) -> RuntimeBundleBuildExecution:
    """Judge the request and, only if BUILDABLE, persist it as one ``BUILDING`` bundle.

    The caller owns the transaction, so any error rolls back the manifest, the bundle and every
    member together.  A ``REJECTED`` outcome performs no write at all -- not even the manifest.
    """
    outcome = evaluate_runtime_bundle_build(request)
    if outcome.decision is not RuntimeBundleBuildDecision.BUILDABLE:
        return RuntimeBundleBuildExecution(outcome=outcome, persisted=None)

    # A BUILDABLE outcome always carries these; the repository re-checks it and additionally
    # verifies that the rows below recompute to outcome.bundle_manifest_hash, so the judgment
    # cannot be bypassed by handing over different rows.
    configuration = outcome.configuration
    if configuration is None or outcome.manifest_hash is None or outcome.bundle_manifest_hash is None:
        raise AssertionError("BUILDABLE outcome must carry a configuration and both hashes")

    repository = RagRuntimeRepository(session)
    persisted = await repository.build_runtime_bundle(
        outcome=outcome,
        manifest=RagRuntimeExecutionManifestCreate(
            manifest_key=request.execution_manifest.manifest_key,
            manifest_version=request.execution_manifest.manifest_version,
            manifest_hash=outcome.manifest_hash,
            schema_version=request.execution_manifest.schema_version,
            git_commit_sha=request.execution_manifest.git_commit_sha,
            worker_artifact_ref=request.execution_manifest.worker_artifact_ref,
            model_ref=request.execution_manifest.model_ref,
            prompt_ref=request.execution_manifest.prompt_ref,
            parser_ref=request.execution_manifest.parser_ref,
            resolver_ref=request.execution_manifest.resolver_ref,
            guard_policy_ref=request.execution_manifest.guard_policy_ref,
        ),
        bundle=_bundle_create(request, configuration, bundle_manifest_hash=outcome.bundle_manifest_hash),
        bundle_sources=tuple(_source_create(member) for member in configuration.source_members),
    )
    return RuntimeBundleBuildExecution(outcome=outcome, persisted=persisted)


async def load_persisted_bundle_configuration(
    session: AsyncSession,
    bundle_id: UUID,
) -> RuntimeBundleCanonicalConfiguration | None:
    """Rebuild the canonical configuration from stored rows.

    This is the read half of the round trip: the values that entered ``bundle_manifest_hash`` are
    all columns, so they can be read back and re-hashed.
    """
    repository = RagRuntimeRepository(session)
    bundle = await repository.get_release_bundle_by_id(bundle_id)
    if bundle is None:
        return None
    manifest = await repository.get_execution_manifest_by_id(bundle.execution_manifest_id)
    if manifest is None:
        return None
    members = await repository.list_bundle_sources(bundle.id)

    artifact_members: list[RuntimeBundleArtifactMemberIdentity] = []
    for prefix, kind in _ARTIFACT_KIND_BY_PREFIX.items():
        ref = getattr(bundle, f"{prefix}_ref")
        version = getattr(bundle, f"{prefix}_version")
        if ref is None or version is None:
            continue
        artifact_members.append(
            RuntimeBundleArtifactMemberIdentity(
                artifact_kind=kind,
                artifact_ref=ref,
                artifact_version=version,
                manifest_hash=getattr(bundle, f"{prefix}_manifest_hash") if prefix in _HASHED_PREFIXES else None,
            )
        )

    return RuntimeBundleCanonicalConfiguration(
        environment_code=bundle.environment_code,
        # Recomputed from the stored manifest's own fields, not read from its manifest_hash
        # column: a stored hash that disagrees with its own row must surface as a verification
        # failure rather than be echoed back and silently confirm itself.
        execution_manifest_hash=recomputed_execution_manifest_hash(manifest),
        catalog_version=bundle.catalog_version,
        catalog_manifest_hash=bundle.catalog_manifest_hash,
        source_members=tuple(
            RuntimeBundleSourceMemberIdentity(
                source_snapshot_id=str(member.source_snapshot_id),
                source_purpose=RuntimeBundleMemberPurpose(member.source_purpose.value),
                source_version=member.source_version,
                canonical_checksum=member.canonical_checksum,
                approval_version=member.approval_version,
                scope_policy_hash=member.scope_policy_hash,
                freshness_policy_hash=member.freshness_policy_hash,
                required=member.required,
                selected_for_operation=member.selected_for_operation,
            )
            for member in members
        ),
        artifact_members=tuple(artifact_members),
    )


async def verify_persisted_bundle_manifest_hash(session: AsyncSession, bundle_id: UUID) -> bool:
    """Recompute ``bundle_manifest_hash`` from stored rows and compare it with the stored value.

    This is what makes the hash a verifiable identity rather than an opaque token, and it is also
    how member-set immutability is detected: appending or altering a member changes the recomputed
    hash, so the comparison fails.

    The stored manifest is checked against its own fields first.  A manifest row whose
    ``manifest_hash`` column disagrees with its ``model_ref``/``prompt_ref``/... is corrupt on its
    own terms, and reporting that directly is clearer than only observing the downstream bundle
    hash mismatch it causes.
    """
    repository = RagRuntimeRepository(session)
    bundle = await repository.get_release_bundle_by_id(bundle_id)
    if bundle is None:
        return False
    manifest = await repository.get_execution_manifest_by_id(bundle.execution_manifest_id)
    if manifest is None:
        return False
    if recomputed_execution_manifest_hash(manifest) != manifest.manifest_hash:
        return False
    configuration = await load_persisted_bundle_configuration(session, bundle_id)
    if configuration is None:
        return False
    return canonical_runtime_bundle_manifest_hash(configuration) == bundle.bundle_manifest_hash


def _bundle_create(
    request: RuntimeBundleBuildRequest,
    configuration: RuntimeBundleCanonicalConfiguration,
    *,
    bundle_manifest_hash: str,
) -> RagRuntimeReleaseBundleCreate:
    artifact_columns: dict[str, str | None] = {}
    for kind, prefix in _ARTIFACT_COLUMN_PREFIX.items():
        member = next((item for item in configuration.artifact_members if item.artifact_kind is kind), None)
        artifact_columns[f"{prefix}_ref"] = member.artifact_ref if member else None
        artifact_columns[f"{prefix}_version"] = member.artifact_version if member else None
        if prefix in _HASHED_PREFIXES:
            artifact_columns[f"{prefix}_manifest_hash"] = member.manifest_hash if member else None
    return RagRuntimeReleaseBundleCreate(
        bundle_key=request.bundle_key,
        bundle_version=request.bundle_version,
        # Replaced with the real manifest id inside the repository transaction.
        execution_manifest_id=uuid4(),
        bundle_manifest_hash=bundle_manifest_hash,
        environment_code=configuration.environment_code,
        catalog_version=configuration.catalog_version,
        catalog_manifest_hash=configuration.catalog_manifest_hash,
        governance_revision_ref=request.governance_revision_ref,
        created_by=request.created_by,
        **artifact_columns,  # type: ignore[arg-type]
    )


def _source_create(member: RuntimeBundleSourceMemberIdentity) -> RagRuntimeBundleSourceCreate:
    return RagRuntimeBundleSourceCreate(
        # Replaced with the real bundle id inside the repository transaction.
        bundle_id=uuid4(),
        source_snapshot_id=UUID(member.source_snapshot_id),
        source_purpose=RagRuntimeSourcePurpose(member.source_purpose.value),
        source_version=member.source_version,
        canonical_checksum=member.canonical_checksum,
        approval_version=member.approval_version,
        scope_policy_hash=member.scope_policy_hash,
        freshness_policy_hash=member.freshness_policy_hash,
        required=member.required,
        selected_for_operation=member.selected_for_operation,
    )
