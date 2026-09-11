from dataclasses import asdict, dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberIdentity,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildOutcome,
    RuntimeBundleCanonicalConfiguration,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberIdentity,
    RuntimeExecutionManifestInput,
    canonical_execution_manifest_hash,
    canonical_runtime_bundle_manifest_hash,
)
from app.models.prescriptions import PrescriptionVersionMedication
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
from app.models.rag_source import RagSourceSnapshot


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
    question_digest: str
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
    """Every field behind ``bundle_manifest_hash`` is required, so the hash stays recomputable.

    ``environment_code``, ``catalog_version``, ``catalog_manifest_hash`` and each artifact
    ``*_version`` enter the hash; storing a bundle without them would make the hash an opaque
    token that no later read can re-verify.
    """

    bundle_key: str
    bundle_version: str
    execution_manifest_id: UUID
    bundle_manifest_hash: str
    environment_code: str
    catalog_version: str
    catalog_manifest_hash: str
    bundle_status: RagRuntimeBundleStatus = RagRuntimeBundleStatus.BUILDING
    candidate_index_ref: str | None = None
    candidate_index_version: str | None = None
    candidate_index_manifest_hash: str | None = None
    knowledge_index_ref: str | None = None
    knowledge_index_version: str | None = None
    knowledge_index_manifest_hash: str | None = None
    rule_set_ref: str | None = None
    rule_set_version: str | None = None
    guideline_set_ref: str | None = None
    guideline_set_version: str | None = None
    safety_policy_ref: str | None = None
    safety_policy_version: str | None = None
    governance_revision_ref: str | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class RagRuntimeBundleSourceCreate:
    bundle_id: UUID
    source_snapshot_id: UUID
    source_purpose: RagRuntimeSourcePurpose
    source_version: str
    canonical_checksum: str
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    required: bool = True
    selected_for_operation: bool = True


_ARTIFACT_KIND_BY_COLUMN_PREFIX = {
    "candidate_index": RuntimeBundleArtifactKind.CANDIDATE_INDEX,
    "knowledge_index": RuntimeBundleArtifactKind.KNOWLEDGE_INDEX,
    "rule_set": RuntimeBundleArtifactKind.RULE_SET,
    "guideline_set": RuntimeBundleArtifactKind.GUIDELINE_SET,
    "safety_policy": RuntimeBundleArtifactKind.SAFETY_POLICY,
}
_HASHED_COLUMN_PREFIXES = frozenset({"candidate_index", "knowledge_index"})


class RagRuntimeBundleBuildError(RuntimeError):
    """The requested bundle write cannot produce a verifiable immutable bundle."""


class RagRuntimeBundleNotBuildableError(RagRuntimeBundleBuildError):
    """The kernel did not authorise this write, or the rows do not match what it judged."""


class RagRuntimeBundleSourceVersionMismatchError(RagRuntimeBundleBuildError):
    """A member claims a ``source_version`` its snapshot does not have."""


MANIFEST_IDENTITY_FIELDS = (
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


def execution_manifest_input_from(source: object) -> RuntimeExecutionManifestInput:
    """Project a manifest Create DTO or a stored manifest row onto the kernel's hash input.

    Both shapes carry identically named attributes, so one projection serves the write path and
    the re-read path.  Recomputing from these fields -- rather than trusting a supplied
    ``manifest_hash`` string -- is what keeps the execution axis bound to the hash.
    """
    return RuntimeExecutionManifestInput(**{field: getattr(source, field) for field in MANIFEST_IDENTITY_FIELDS})


def recomputed_execution_manifest_hash(source: object) -> str:
    """Return the manifest hash implied by the manifest's own field values."""
    return canonical_execution_manifest_hash(execution_manifest_input_from(source))


def _assert_rows_match_outcome(
    outcome: RuntimeBundleBuildOutcome,
    *,
    manifest: RagRuntimeExecutionManifestCreate,
    bundle: RagRuntimeReleaseBundleCreate,
    bundle_sources: tuple[RagRuntimeBundleSourceCreate, ...],
) -> None:
    """Refuse any write the kernel did not authorise, or that differs from what it judged.

    Taking the outcome as a required argument is what makes the judgment enforced rather than
    advisory: there is no way to reach persistence without one.  Comparing the recomputed hash of
    the rows about to be written against ``outcome.bundle_manifest_hash`` closes the remaining
    gap -- a caller cannot pass a BUILDABLE outcome and then hand over different rows.
    """
    if outcome.decision is not RuntimeBundleBuildDecision.BUILDABLE:
        raise RagRuntimeBundleNotBuildableError(
            f"kernel 판정이 {outcome.decision.value}이므로 저장하지 않습니다. "
            f"rejection_reasons={[reason.value for reason in outcome.rejection_reasons]} "
            f"validation_codes={[code.value for code in outcome.validation_codes]}"
        )
    if outcome.configuration is None or outcome.manifest_hash is None or outcome.bundle_manifest_hash is None:
        raise RagRuntimeBundleNotBuildableError("BUILDABLE 판정에 configuration과 두 hash가 모두 있어야 합니다.")
    if manifest.manifest_hash != outcome.manifest_hash:
        raise RagRuntimeBundleNotBuildableError("manifest_hash가 판정 결과와 다릅니다.")
    # A supplied manifest_hash is just a string: comparing it to the judged hash does not prove
    # the manifest fields are the ones that were judged.  Without this recomputation a caller
    # could keep the judged hash and swap model_ref/prompt_ref, binding a different execution
    # axis to an approved hash.
    manifest_recomputed = recomputed_execution_manifest_hash(manifest)
    if manifest_recomputed != outcome.manifest_hash:
        raise RagRuntimeBundleNotBuildableError(
            "Execution Manifest 내용이 판정된 구성과 다릅니다. "
            f"필드 기준 재계산 {manifest_recomputed[:12]}… != 판정 {outcome.manifest_hash[:12]}…"
        )
    if bundle.bundle_manifest_hash != outcome.bundle_manifest_hash:
        raise RagRuntimeBundleNotBuildableError("bundle_manifest_hash가 판정 결과와 다릅니다.")

    recomputed = canonical_runtime_bundle_manifest_hash(
        _configuration_from_rows(bundle, bundle_sources, execution_manifest_hash=manifest.manifest_hash)
    )
    if recomputed != outcome.bundle_manifest_hash:
        raise RagRuntimeBundleNotBuildableError(
            "저장하려는 행이 판정된 구성과 다릅니다. "
            f"행 기준 재계산 {recomputed[:12]}… != 판정 {outcome.bundle_manifest_hash[:12]}…"
        )


def _configuration_from_rows(
    bundle: RagRuntimeReleaseBundleCreate,
    bundle_sources: tuple[RagRuntimeBundleSourceCreate, ...],
    *,
    execution_manifest_hash: str,
) -> RuntimeBundleCanonicalConfiguration:
    """Project the rows about to be written onto the canonical configuration.

    Reading only from the Create DTOs is deliberate: it proves the configuration is reconstructible
    from column values alone, which is the same property the post-write verification relies on.
    """
    artifact_members = []
    for prefix, kind in _ARTIFACT_KIND_BY_COLUMN_PREFIX.items():
        ref = getattr(bundle, f"{prefix}_ref")
        version = getattr(bundle, f"{prefix}_version")
        if ref is None or version is None:
            continue
        artifact_members.append(
            RuntimeBundleArtifactMemberIdentity(
                artifact_kind=kind,
                artifact_ref=ref,
                artifact_version=version,
                manifest_hash=(
                    getattr(bundle, f"{prefix}_manifest_hash") if prefix in _HASHED_COLUMN_PREFIXES else None
                ),
            )
        )
    return RuntimeBundleCanonicalConfiguration(
        environment_code=bundle.environment_code,
        execution_manifest_hash=execution_manifest_hash,
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
            for member in bundle_sources
        ),
        artifact_members=tuple(artifact_members),
    )


class RagRuntimeExecutionManifestConflictError(RagRuntimeBundleBuildError):
    """A stored manifest shares the requested hash but pins a different execution axis."""


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
        field for field in MANIFEST_IDENTITY_FIELDS if getattr(stored, field) != getattr(requested, field)
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
        execution_version_id = await self.session.scalar(
            select(AiJobExecutionContext.prescription_version_id).where(
                AiJobExecutionContext.id == payload.execution_context_id
            )
        )
        if execution_version_id is None:
            raise ValueError("execution context is required before pinning identification")

        matched_identification = await self.session.scalar(
            select(MedicationIdentification.id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationIdentification.prescription_version_medication_id,
            )
            .where(
                MedicationIdentification.id == payload.medication_identification_id,
                MedicationIdentification.prescription_version_medication_id
                == payload.prescription_version_medication_id,
                MedicationIdentification.status == MedicationIdentificationStatus.MATCHED,
                PrescriptionVersionMedication.prescription_version_id == execution_version_id,
            )
        )
        if matched_identification is None:
            raise ValueError(
                "execution context can pin only MATCHED identification for the same medication and prescription version"
            )

        identification = AiJobExecutionIdentification(
            **asdict(payload),
            prescription_version_id=execution_version_id,
        )
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

    async def get_execution_manifest_by_id(self, manifest_id: UUID) -> RagRuntimeExecutionManifest | None:
        result = await self.session.execute(
            select(RagRuntimeExecutionManifest).where(RagRuntimeExecutionManifest.id == manifest_id)
        )
        return result.scalar_one_or_none()

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

    async def get_release_bundle_by_id(self, bundle_id: UUID) -> RagRuntimeReleaseBundle | None:
        result = await self.session.execute(
            select(RagRuntimeReleaseBundle).where(RagRuntimeReleaseBundle.id == bundle_id)
        )
        return result.scalar_one_or_none()

    async def get_release_bundle_by_manifest_hash(self, manifest_hash: str) -> RagRuntimeReleaseBundle | None:
        result = await self.session.execute(
            select(RagRuntimeReleaseBundle).where(RagRuntimeReleaseBundle.bundle_manifest_hash == manifest_hash)
        )
        return result.scalar_one_or_none()

    async def _assert_member_versions_exist(self, bundle_sources: tuple[RagRuntimeBundleSourceCreate, ...]) -> None:
        """Reject any member whose ``(source_snapshot_id, source_version)`` pair does not exist.

        This replaces a composite FK onto ``uq_rag_source_snapshot_id_version``.  The FK would
        enforce the same rule in the database, but it made this migration a hard dependant of
        #369's unique constraint -- #369's downgrade could then no longer drop it, breaking 11 of
        its tests.  #398 moved integrity enforcement from the database into Python, and this
        follows that direction: one query, fail-closed, inside the same transaction as the write.
        """
        wanted = {(member.source_snapshot_id, member.source_version) for member in bundle_sources}
        result = await self.session.execute(
            select(RagSourceSnapshot.id, RagSourceSnapshot.source_version).where(
                RagSourceSnapshot.id.in_({snapshot_id for snapshot_id, _ in wanted})
            )
        )
        existing = {(row[0], row[1]) for row in result}
        missing = sorted(f"{snapshot_id}@{source_version}" for snapshot_id, source_version in wanted - existing)
        if missing:
            raise RagRuntimeBundleSourceVersionMismatchError(
                "member가 snapshot에 없는 source_version을 주장합니다: " + ", ".join(missing)
            )

    async def _create_bundle_source(self, payload: RagRuntimeBundleSourceCreate) -> RagRuntimeBundleSource:
        """Private on purpose: members are only ever written by :meth:`build_runtime_bundle`.

        A public member-insert would let a caller append to an already-built bundle, which breaks
        the member-set immutability that ``bundle_manifest_hash`` is supposed to identify.
        """
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
        outcome: RuntimeBundleBuildOutcome,
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
        _assert_rows_match_outcome(outcome, bundle=bundle, manifest=manifest, bundle_sources=bundle_sources)
        await self._assert_member_versions_exist(bundle_sources)
        if not bundle_sources:
            raise RagRuntimeBundleBuildError(
                "member 없는 Bundle은 저장할 수 없습니다. bundle_manifest_hash가 빈 member set을 "
                "가리키면 평가·실행 대상 동일성을 재검증할 수 없습니다."
            )

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
            [
                await self._create_bundle_source(replace(member, bundle_id=created_bundle.id))
                for member in bundle_sources
            ]
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
