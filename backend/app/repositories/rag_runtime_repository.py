from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_evaluation import EvaluationDecisionStatus
from app.models.rag_runtime import (
    RagReleaseEvaluationApproval,
    RagRuntimeApprovalStatus,
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironment,
    RagRuntimeEnvironmentStatus,
    RagRuntimeEnvironmentTransition,
    RagRuntimeEnvironmentTransitionKind,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
    RagRuntimeSourcePurpose,
)


@dataclass(frozen=True, slots=True)
class RagRuntimeExecutionManifestCreate:
    manifest_key: str
    manifest_version: str
    manifest_hash: str
    schema_version: str
    git_commit_sha: str
    worker_artifact_ref: str | None = None
    model_ref: str | None = None
    prompt_ref: str | None = None
    parser_ref: str | None = None
    resolver_ref: str | None = None
    guard_policy_ref: str | None = None
    metadata_json: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RagRuntimeReleaseBundleCreate:
    bundle_key: str
    bundle_version: str
    execution_manifest_id: UUID
    bundle_manifest_hash: str
    bundle_status: RagRuntimeBundleStatus = RagRuntimeBundleStatus.BUILDING
    candidate_index_ref: str | None = None
    candidate_index_manifest_hash: str | None = None
    knowledge_index_ref: str | None = None
    knowledge_index_manifest_hash: str | None = None
    rule_set_ref: str | None = None
    guideline_set_ref: str | None = None
    safety_policy_ref: str | None = None
    governance_revision_ref: str | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class RagRuntimeBundleSourceCreate:
    bundle_id: UUID
    source_snapshot_id: UUID
    source_purpose: RagRuntimeSourcePurpose
    required: bool = True
    selected_for_operation: bool = True


@dataclass(frozen=True, slots=True)
class RagRuntimeEnvironmentCreate:
    environment_code: str
    environment_status: RagRuntimeEnvironmentStatus = RagRuntimeEnvironmentStatus.SUSPENDED
    active_bundle_id: UUID | None = None
    active_bundle_manifest_hash: str | None = None
    governance_revision_ref: str | None = None
    environment_revision: int = 1
    safety_epoch: int = 1


@dataclass(frozen=True, slots=True)
class RagRuntimeEnvironmentTransitionCreate:
    environment_id: UUID
    transition_kind: RagRuntimeEnvironmentTransitionKind
    environment_revision: int
    safety_epoch: int
    guard_decision_ref: str
    from_bundle_id: UUID | None = None
    from_bundle_manifest_hash: str | None = None
    to_bundle_id: UUID | None = None
    to_bundle_manifest_hash: str | None = None
    governance_revision_ref: str | None = None
    transition_reason_code: str | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class RagReleaseEvaluationApprovalCreate:
    bundle_id: UUID
    eval_run_id: UUID
    approval_scope: str
    eval_decision_status: EvaluationDecisionStatus
    approval_status: RagRuntimeApprovalStatus = RagRuntimeApprovalStatus.PENDING
    approved_by: str | None = None
    approved_at: datetime | None = None


class RagRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_execution_manifest(
        self,
        payload: RagRuntimeExecutionManifestCreate,
    ) -> RagRuntimeExecutionManifest:
        manifest = RagRuntimeExecutionManifest(**asdict(payload))
        self.session.add(manifest)
        await self.session.flush()
        return manifest

    async def get_execution_manifest_by_hash(self, manifest_hash: str) -> RagRuntimeExecutionManifest | None:
        result = await self.session.execute(
            select(RagRuntimeExecutionManifest).where(RagRuntimeExecutionManifest.manifest_hash == manifest_hash)
        )
        return result.scalar_one_or_none()

    async def create_release_bundle(self, payload: RagRuntimeReleaseBundleCreate) -> RagRuntimeReleaseBundle:
        bundle = RagRuntimeReleaseBundle(**asdict(payload))
        self.session.add(bundle)
        await self.session.flush()
        return bundle

    async def get_release_bundle_by_version(
        self,
        *,
        bundle_key: str,
        bundle_version: str,
    ) -> RagRuntimeReleaseBundle | None:
        result = await self.session.execute(
            select(RagRuntimeReleaseBundle).where(
                RagRuntimeReleaseBundle.bundle_key == bundle_key,
                RagRuntimeReleaseBundle.bundle_version == bundle_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_release_bundle_by_manifest_hash(self, manifest_hash: str) -> RagRuntimeReleaseBundle | None:
        result = await self.session.execute(
            select(RagRuntimeReleaseBundle).where(RagRuntimeReleaseBundle.bundle_manifest_hash == manifest_hash)
        )
        return result.scalar_one_or_none()

    async def create_bundle_source(self, payload: RagRuntimeBundleSourceCreate) -> RagRuntimeBundleSource:
        bundle_source = RagRuntimeBundleSource(**asdict(payload))
        self.session.add(bundle_source)
        await self.session.flush()
        return bundle_source

    async def list_bundle_sources(self, bundle_id: UUID) -> list[RagRuntimeBundleSource]:
        result = await self.session.execute(
            select(RagRuntimeBundleSource)
            .where(RagRuntimeBundleSource.bundle_id == bundle_id)
            .order_by(RagRuntimeBundleSource.created_at, RagRuntimeBundleSource.id)
        )
        return list(result.scalars().all())

    async def create_environment(self, payload: RagRuntimeEnvironmentCreate) -> RagRuntimeEnvironment:
        environment = RagRuntimeEnvironment(**asdict(payload))
        self.session.add(environment)
        await self.session.flush()
        return environment

    async def get_environment_by_code(self, environment_code: str) -> RagRuntimeEnvironment | None:
        result = await self.session.execute(
            select(RagRuntimeEnvironment).where(RagRuntimeEnvironment.environment_code == environment_code)
        )
        return result.scalar_one_or_none()

    async def create_environment_transition(
        self,
        payload: RagRuntimeEnvironmentTransitionCreate,
    ) -> RagRuntimeEnvironmentTransition:
        transition = RagRuntimeEnvironmentTransition(**asdict(payload))
        self.session.add(transition)
        await self.session.flush()
        return transition

    async def list_environment_transitions(self, environment_id: UUID) -> list[RagRuntimeEnvironmentTransition]:
        result = await self.session.execute(
            select(RagRuntimeEnvironmentTransition)
            .where(RagRuntimeEnvironmentTransition.environment_id == environment_id)
            .order_by(RagRuntimeEnvironmentTransition.created_at, RagRuntimeEnvironmentTransition.id)
        )
        return list(result.scalars().all())

    async def create_evaluation_approval(
        self,
        payload: RagReleaseEvaluationApprovalCreate,
    ) -> RagReleaseEvaluationApproval:
        approval = RagReleaseEvaluationApproval(**asdict(payload))
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def list_bundle_evaluation_approvals(self, bundle_id: UUID) -> list[RagReleaseEvaluationApproval]:
        result = await self.session.execute(
            select(RagReleaseEvaluationApproval)
            .where(RagReleaseEvaluationApproval.bundle_id == bundle_id)
            .order_by(RagReleaseEvaluationApproval.created_at, RagReleaseEvaluationApproval.id)
        )
        return list(result.scalars().all())
