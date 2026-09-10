from dataclasses import asdict, dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_candidate import MedicationIdentification, MedicationIdentificationStatus
from app.models.rag_evaluation import EvaluationDecisionStatus
from app.models.rag_runtime import (
    AiJobExecutionContext,
    AiJobExecutionIdentification,
    AiJobIntakeContext,
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
class AiJobIntakeContextCreate:
    ai_job_id: UUID
    chat_message_id: UUID
    prescription_version_id: UUID
    runtime_environment_id: UUID
    runtime_environment_revision: int
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    question_digest: str | None = None
    patient_context_digest: str | None = None
    context_schema_version: str = "ai-job-intake-context@1"


@dataclass(frozen=True, slots=True)
class AiJobExecutionContextCreate:
    ai_job_id: UUID
    prescription_version_id: UUID
    runtime_environment_id: UUID
    runtime_environment_revision: int
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    intake_context_id: UUID | None = None
    guide_id: UUID | None = None
    chat_message_id: UUID | None = None
    patient_context_digest: str | None = None
    source_scope_manifest_hash: str | None = None
    context_schema_version: str = "ai-job-execution-context@1"


@dataclass(frozen=True, slots=True)
class AiJobExecutionIdentificationCreate:
    execution_context_id: UUID
    medication_identification_id: UUID
    prescription_version_medication_id: UUID


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


class RagRuntimeExecutionManifestConflictError(RuntimeError):
    """A stored manifest shares the requested hash but pins a different execution axis."""


_MANIFEST_IDENTITY_FIELDS = (
    "manifest_key",
    "manifest_version",
    "schema_version",
    "git_commit_sha",
    "worker_artifact_ref",
    "model_ref",
    "prompt_ref",
    "parser_ref",
    "resolver_ref",
    "guard_policy_ref",
)


def _assert_manifest_matches(
    stored: RagRuntimeExecutionManifest,
    requested: RagRuntimeExecutionManifestCreate,
) -> None:
    """Fail closed when a manifest hash is reused for different content.

    ``manifest_hash`` is supplied by the caller, so a hash that matches an existing row does not
    by itself prove the rows describe the same execution axis.  Reusing a mismatched manifest
    would silently pin the bundle to something the caller never asked for, which is exactly the
    evaluated-vs-executed drift this issue exists to prevent.
    """
    mismatched = tuple(
        field for field in _MANIFEST_IDENTITY_FIELDS if getattr(stored, field) != getattr(requested, field)
    )
    if mismatched:
        raise RagRuntimeExecutionManifestConflictError(
            f"manifest_hash {requested.manifest_hash} is already stored with different {', '.join(mismatched)}"
        )


@dataclass(frozen=True, slots=True)
class RagRuntimeBundleBuildResult:
    """What a single RAG-12A build transaction persisted."""

    execution_manifest: RagRuntimeExecutionManifest
    bundle: RagRuntimeReleaseBundle
    bundle_sources: tuple[RagRuntimeBundleSource, ...]
    execution_manifest_reused: bool


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
    expected_environment_revision: int
    expected_safety_epoch: int
    expected_active_bundle_id: UUID | None
    expected_active_bundle_manifest_hash: str | None
    expected_governance_revision_ref: str | None
    guard_decision_ref: str
    target_bundle_id: UUID | None = None
    target_bundle_manifest_hash: str | None = None
    transition_reason_code: str | None = None
    created_by: str | None = None


class RuntimeEnvironmentTransitionConflictError(ValueError):
    pass


class RuntimeEnvironmentTransitionInvalidError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RagReleaseEvaluationApprovalCreate:
    bundle_id: UUID
    bundle_manifest_hash: str
    eval_run_id: UUID
    approval_scope: str
    eval_decision_status: EvaluationDecisionStatus
    approval_status: RagRuntimeApprovalStatus = RagRuntimeApprovalStatus.PENDING
    approved_by: str | None = None
    approved_at: datetime | None = None


class RagRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_intake_context(self, payload: AiJobIntakeContextCreate) -> AiJobIntakeContext:
        context = AiJobIntakeContext(**asdict(payload))
        self.session.add(context)
        await self.session.flush()
        return context

    async def get_intake_context_by_job(self, ai_job_id: UUID) -> AiJobIntakeContext | None:
        result = await self.session.execute(select(AiJobIntakeContext).where(AiJobIntakeContext.ai_job_id == ai_job_id))
        return result.scalar_one_or_none()

    async def create_execution_context(self, payload: AiJobExecutionContextCreate) -> AiJobExecutionContext:
        context = AiJobExecutionContext(**asdict(payload))
        self.session.add(context)
        await self.session.flush()
        return context

    async def get_execution_context_by_job(self, ai_job_id: UUID) -> AiJobExecutionContext | None:
        result = await self.session.execute(
            select(AiJobExecutionContext).where(AiJobExecutionContext.ai_job_id == ai_job_id)
        )
        return result.scalar_one_or_none()

    async def create_execution_identification(
        self,
        payload: AiJobExecutionIdentificationCreate,
    ) -> AiJobExecutionIdentification:
        matched_identification = await self.session.scalar(
            select(MedicationIdentification.id).where(
                MedicationIdentification.id == payload.medication_identification_id,
                MedicationIdentification.prescription_version_medication_id
                == payload.prescription_version_medication_id,
                MedicationIdentification.status == MedicationIdentificationStatus.MATCHED,
            )
        )
        if matched_identification is None:
            raise ValueError("execution context can pin only MATCHED identification for the same medication")

        identification = AiJobExecutionIdentification(**asdict(payload))
        self.session.add(identification)
        await self.session.flush()
        return identification

    async def list_execution_identifications(
        self,
        execution_context_id: UUID,
    ) -> list[AiJobExecutionIdentification]:
        result = await self.session.execute(
            select(AiJobExecutionIdentification)
            .where(AiJobExecutionIdentification.execution_context_id == execution_context_id)
            .order_by(AiJobExecutionIdentification.created_at, AiJobExecutionIdentification.id)
        )
        return list(result.scalars().all())

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

    async def build_runtime_bundle(
        self,
        *,
        manifest: RagRuntimeExecutionManifestCreate,
        bundle: RagRuntimeReleaseBundleCreate,
        bundle_sources: tuple[RagRuntimeBundleSourceCreate, ...],
    ) -> RagRuntimeBundleBuildResult:
        """Persist one ``BUILDING`` bundle with its full member set (RAG-12A, Issue #175).

        The caller owns the transaction, so a raised error rolls back the manifest, the bundle
        and every member together -- there is no partial-bundle path.  Eligibility is decided
        before this call by ``ai_worker.tasks.rag.runtime_bundle_builder``; this method persists
        a decision, it does not re-make one.

        Two boundaries are structural rather than checked:

        - ``bundle_status`` is forced to ``BUILDING``.  ``READY``, ``RETIRED`` and the
          environment pointer belong to RAG-17 (#180), so this method cannot write them.
        - ``rag_runtime_environment`` and ``rag_runtime_environment_transition`` are neither read
          nor written, so a build failure has no path to change an existing active pointer.

        Members are created once here and never updated: the repository exposes no member update
        or delete, which is how "``BUILDING`` member 입력을 임의로 update하지 못한다" holds.

        Raises:
            RagRuntimeExecutionManifestConflictError: a manifest already stores this hash but
                describes a different execution axis, so reusing it would bind the bundle to a
                manifest the caller did not pin.
        """
        existing_manifest = await self.get_execution_manifest_by_hash(manifest.manifest_hash)
        if existing_manifest is not None:
            _assert_manifest_matches(existing_manifest, manifest)
        execution_manifest = existing_manifest or await self.create_execution_manifest(manifest)

        created_bundle = await self.create_release_bundle(
            replace(
                bundle,
                execution_manifest_id=execution_manifest.id,
                bundle_status=RagRuntimeBundleStatus.BUILDING,
            )
        )
        created_sources = tuple(
            [await self.create_bundle_source(replace(member, bundle_id=created_bundle.id)) for member in bundle_sources]
        )
        return RagRuntimeBundleBuildResult(
            execution_manifest=execution_manifest,
            bundle=created_bundle,
            bundle_sources=created_sources,
            execution_manifest_reused=existing_manifest is not None,
        )

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

    async def transition_environment(
        self,
        payload: RagRuntimeEnvironmentTransitionCreate,
    ) -> RagRuntimeEnvironmentTransition:
        async with self.session.begin_nested():
            environment = await self.session.scalar(
                select(RagRuntimeEnvironment)
                .where(RagRuntimeEnvironment.id == payload.environment_id)
                .with_for_update(of=RagRuntimeEnvironment)
                .execution_options(populate_existing=True)
            )
            if environment is None:
                raise RuntimeEnvironmentTransitionInvalidError("Runtime environment unavailable")
            self._validate_expected_environment(environment, payload)
            target = await self._resolve_transition_target(environment, payload)
            next_status = self._next_environment_status(environment, payload, target)
            next_revision = environment.environment_revision + 1
            from_bundle_id = environment.active_bundle_id
            from_bundle_hash = environment.active_bundle_manifest_hash
            if target is not None:
                environment.active_bundle_id = target.id
                environment.active_bundle_manifest_hash = target.bundle_manifest_hash
            environment.environment_status = next_status
            environment.environment_revision = next_revision
            transition = RagRuntimeEnvironmentTransition(
                environment_id=environment.id,
                transition_kind=payload.transition_kind,
                from_bundle_id=from_bundle_id,
                from_bundle_manifest_hash=from_bundle_hash,
                to_bundle_id=environment.active_bundle_id,
                to_bundle_manifest_hash=environment.active_bundle_manifest_hash,
                environment_revision=next_revision,
                governance_revision_ref=environment.governance_revision_ref,
                safety_epoch=environment.safety_epoch,
                guard_decision_ref=payload.guard_decision_ref,
                transition_reason_code=payload.transition_reason_code,
                created_by=payload.created_by,
            )
            self.session.add(transition)
            await self.session.flush()
            return transition

    @staticmethod
    def _validate_expected_environment(
        environment: RagRuntimeEnvironment,
        payload: RagRuntimeEnvironmentTransitionCreate,
    ) -> None:
        expected_pointer = (
            payload.expected_active_bundle_id,
            payload.expected_active_bundle_manifest_hash,
        )
        actual_pointer = (environment.active_bundle_id, environment.active_bundle_manifest_hash)
        if (
            payload.expected_environment_revision != environment.environment_revision
            or payload.expected_safety_epoch != environment.safety_epoch
            or payload.expected_governance_revision_ref != environment.governance_revision_ref
            or expected_pointer != actual_pointer
        ):
            raise RuntimeEnvironmentTransitionConflictError("Runtime environment changed")

    async def _resolve_transition_target(
        self,
        environment: RagRuntimeEnvironment,
        payload: RagRuntimeEnvironmentTransitionCreate,
    ) -> RagRuntimeReleaseBundle | None:
        if payload.transition_kind is RagRuntimeEnvironmentTransitionKind.SUSPEND:
            if payload.target_bundle_id is not None or payload.target_bundle_manifest_hash is not None:
                raise RuntimeEnvironmentTransitionInvalidError("Suspend must retain the active bundle")
            return None
        if payload.transition_kind is RagRuntimeEnvironmentTransitionKind.RESUME:
            if payload.target_bundle_id is not None or payload.target_bundle_manifest_hash is not None:
                raise RuntimeEnvironmentTransitionInvalidError("Resume must use the retained active bundle")
            target_id = environment.active_bundle_id
            target_hash = environment.active_bundle_manifest_hash
        elif payload.transition_kind in {
            RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
            RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK,
        }:
            target_id = payload.target_bundle_id
            target_hash = payload.target_bundle_manifest_hash
        else:
            raise RuntimeEnvironmentTransitionInvalidError("Unsupported Runtime transition")
        if (
            payload.transition_kind is RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK
            and target_id is None
            and target_hash is None
        ):
            return None
        if target_id is None or target_hash is None:
            raise RuntimeEnvironmentTransitionInvalidError("Transition target bundle required")
        target = await self.session.scalar(
            select(RagRuntimeReleaseBundle)
            .where(
                RagRuntimeReleaseBundle.id == target_id,
                RagRuntimeReleaseBundle.bundle_manifest_hash == target_hash,
            )
            .with_for_update(of=RagRuntimeReleaseBundle)
            .execution_options(populate_existing=True)
        )
        allowed_statuses = (
            {RagRuntimeBundleStatus.READY, RagRuntimeBundleStatus.RETIRED}
            if payload.transition_kind is RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK
            else {RagRuntimeBundleStatus.READY}
        )
        if target is None or target.bundle_status not in allowed_statuses:
            raise RuntimeEnvironmentTransitionInvalidError("Transition target bundle unavailable")
        self._validate_target_environment(environment, payload, target)
        return target

    @staticmethod
    def _validate_target_environment(
        environment: RagRuntimeEnvironment,
        payload: RagRuntimeEnvironmentTransitionCreate,
        target: RagRuntimeReleaseBundle,
    ) -> None:
        if target.governance_revision_ref != environment.governance_revision_ref:
            raise RuntimeEnvironmentTransitionConflictError("Runtime governance revision changed")
        same_target_error = {
            RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK: "Rollback target must differ from the active bundle",
            RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION: "Activation target is already active",
        }.get(payload.transition_kind)
        if target.id == environment.active_bundle_id and same_target_error is not None:
            raise RuntimeEnvironmentTransitionInvalidError(same_target_error)

    @staticmethod
    def _next_environment_status(
        environment: RagRuntimeEnvironment,
        payload: RagRuntimeEnvironmentTransitionCreate,
        target: RagRuntimeReleaseBundle | None,
    ) -> RagRuntimeEnvironmentStatus:
        transition_kind = payload.transition_kind
        if transition_kind is RagRuntimeEnvironmentTransitionKind.SUSPEND:
            if environment.environment_status is not RagRuntimeEnvironmentStatus.ACTIVE:
                raise RuntimeEnvironmentTransitionInvalidError("Only an active environment can be suspended")
            return RagRuntimeEnvironmentStatus.SUSPENDED
        if transition_kind is RagRuntimeEnvironmentTransitionKind.RESUME:
            if environment.environment_status is not RagRuntimeEnvironmentStatus.SUSPENDED:
                raise RuntimeEnvironmentTransitionInvalidError("Only a suspended environment can be resumed")
            return RagRuntimeEnvironmentStatus.ACTIVE
        if transition_kind is RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK and target is None:
            if environment.environment_status is not RagRuntimeEnvironmentStatus.ACTIVE:
                raise RuntimeEnvironmentTransitionInvalidError("Only an active environment can enter an emergency hold")
            return RagRuntimeEnvironmentStatus.SUSPENDED
        if (
            transition_kind
            in {
                RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
                RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK,
            }
            and target is not None
        ):
            return RagRuntimeEnvironmentStatus.ACTIVE
        raise RuntimeEnvironmentTransitionInvalidError("Unsupported Runtime transition")

    async def list_environment_transitions(self, environment_id: UUID) -> list[RagRuntimeEnvironmentTransition]:
        result = await self.session.execute(
            select(RagRuntimeEnvironmentTransition)
            .where(RagRuntimeEnvironmentTransition.environment_id == environment_id)
            .order_by(
                RagRuntimeEnvironmentTransition.environment_revision,
                RagRuntimeEnvironmentTransition.created_at,
                RagRuntimeEnvironmentTransition.id,
            )
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
