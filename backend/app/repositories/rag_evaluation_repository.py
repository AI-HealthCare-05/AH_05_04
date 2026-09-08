from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_evaluation import (
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalExperiment,
    EvalFailure,
    EvalMetric,
    EvalRun,
    EvaluationDatasetPartition,
    EvaluationDatasetStatus,
    EvaluationDecisionStatus,
    EvaluationExecutionStatus,
    EvaluationExperimentType,
    EvaluationFailureScope,
    EvaluationMetricScope,
    EvaluationVariantRole,
    EvalVariant,
)


def _validate_decision_status_matches_execution(
    *,
    execution_status: EvaluationExecutionStatus,
    decision_status: EvaluationDecisionStatus | None,
) -> None:
    if execution_status == EvaluationExecutionStatus.COMPLETED:
        if decision_status is None:
            raise ValueError("decision_status is required when execution_status is COMPLETED")
        return

    if decision_status is not None:
        raise ValueError("decision_status is allowed only when execution_status is COMPLETED")


@dataclass(frozen=True)
class EvalDatasetCreate:
    dataset_key: str
    dataset_version: str
    display_name: str
    schema_set_id: str
    schema_set_version: str
    schema_set_sha256: str
    manifest_hash: str
    source_classification: str
    dataset_status: EvaluationDatasetStatus = EvaluationDatasetStatus.DRAFT
    manifest_uri: str | None = None
    case_count: int = 0
    created_by: str | None = None
    reviewed_by: str | None = None
    approved_by: str | None = None


@dataclass(frozen=True)
class EvalCaseCreate:
    dataset_id: UUID
    case_key: str
    case_version: str
    partition: EvaluationDatasetPartition
    experiment_type: EvaluationExperimentType
    input_hash: str
    gold_hash: str | None = None
    question_template: str | None = None
    source_segment: str | None = None
    medication_family: str | None = None
    transform_origin: str | None = None
    expected_scope_codes: list[str] | None = None
    expected_outcome_ref: str | None = None


@dataclass(frozen=True)
class EvalExperimentCreate:
    dataset_id: UUID
    experiment_key: str
    experiment_version: str
    experiment_type: EvaluationExperimentType
    policy_ref: str
    policy_hash: str
    metric_set_ref: str | None = None
    rubric_ref: str | None = None
    is_release_gate: bool = False


@dataclass(frozen=True)
class EvalVariantCreate:
    experiment_id: UUID
    variant_key: str
    variant_role: EvaluationVariantRole
    config_hash: str
    runtime_bundle_ref: str | None = None
    source_snapshot_ref: str | None = None
    candidate_index_ref: str | None = None
    knowledge_index_ref: str | None = None
    model_ref: str | None = None
    prompt_ref: str | None = None
    parser_ref: str | None = None
    embedding_ref: str | None = None


@dataclass(frozen=True)
class EvalRunCreate:
    run_key: str
    dataset_id: UUID
    experiment_id: UUID
    variant_id: UUID
    experiment_type: EvaluationExperimentType
    execution_status: EvaluationExecutionStatus
    git_commit_sha: str
    dataset_manifest_hash: str
    decision_status: EvaluationDecisionStatus | None = None
    execution_manifest_hash: str | None = None
    candidate_guard_ref: str | None = None
    blocking_execution_statuses: list[str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class EvalCaseResultCreate:
    run_id: UUID
    case_id: UUID
    dataset_id: UUID
    experiment_type: EvaluationExperimentType
    execution_status: EvaluationExecutionStatus
    decision_status: EvaluationDecisionStatus | None = None
    request_guard_ref: str | None = None
    result_summary_hash: str | None = None
    non_sensitive_summary: dict[str, object] | None = None


@dataclass(frozen=True)
class EvalMetricCreate:
    metric_scope: EvaluationMetricScope
    metric_key: str
    metric_version: str
    metric_area: str
    run_id: UUID | None = None
    case_result_id: UUID | None = None
    numerator: int | None = None
    denominator: int | None = None
    score: Decimal | None = None
    confidence_lower: Decimal | None = None
    confidence_upper: Decimal | None = None
    is_release_blocking: bool = False
    is_diagnostic: bool = False


@dataclass(frozen=True)
class EvalFailureCreate:
    failure_scope: EvaluationFailureScope
    failure_code: str
    failure_area: str
    run_id: UUID | None = None
    case_result_id: UUID | None = None
    context_hash: str | None = None
    non_sensitive_context: dict[str, object] | None = None


class RagEvaluationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_dataset_by_version(self, *, dataset_key: str, dataset_version: str) -> EvalDataset | None:
        result = await self.session.execute(
            select(EvalDataset).where(
                EvalDataset.dataset_key == dataset_key,
                EvalDataset.dataset_version == dataset_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_dataset_by_manifest_hash(self, *, manifest_hash: str) -> EvalDataset | None:
        result = await self.session.execute(select(EvalDataset).where(EvalDataset.manifest_hash == manifest_hash))
        return result.scalar_one_or_none()

    async def get_case_by_key(self, *, dataset_id: UUID, case_key: str) -> EvalCase | None:
        result = await self.session.execute(
            select(EvalCase).where(
                EvalCase.dataset_id == dataset_id,
                EvalCase.case_key == case_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_experiment_by_version(
        self,
        *,
        experiment_key: str,
        experiment_version: str,
    ) -> EvalExperiment | None:
        result = await self.session.execute(
            select(EvalExperiment).where(
                EvalExperiment.experiment_key == experiment_key,
                EvalExperiment.experiment_version == experiment_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_variant_by_key(self, *, experiment_id: UUID, variant_key: str) -> EvalVariant | None:
        result = await self.session.execute(
            select(EvalVariant).where(
                EvalVariant.experiment_id == experiment_id,
                EvalVariant.variant_key == variant_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_variant_by_config_hash(self, *, experiment_id: UUID, config_hash: str) -> EvalVariant | None:
        result = await self.session.execute(
            select(EvalVariant).where(
                EvalVariant.experiment_id == experiment_id,
                EvalVariant.config_hash == config_hash,
            )
        )
        return result.scalar_one_or_none()

    async def get_run_by_key(self, *, run_key: str) -> EvalRun | None:
        result = await self.session.execute(select(EvalRun).where(EvalRun.run_key == run_key))
        return result.scalar_one_or_none()

    async def get_case_result(self, *, run_id: UUID, case_id: UUID) -> EvalCaseResult | None:
        result = await self.session.execute(
            select(EvalCaseResult).where(
                EvalCaseResult.run_id == run_id,
                EvalCaseResult.case_id == case_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_run_metric(
        self,
        *,
        run_id: UUID,
        metric_key: str,
        metric_version: str,
    ) -> EvalMetric | None:
        result = await self.session.execute(
            select(EvalMetric).where(
                EvalMetric.run_id == run_id,
                EvalMetric.case_result_id.is_(None),
                EvalMetric.metric_key == metric_key,
                EvalMetric.metric_version == metric_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_case_metric(
        self,
        *,
        case_result_id: UUID,
        metric_key: str,
        metric_version: str,
    ) -> EvalMetric | None:
        result = await self.session.execute(
            select(EvalMetric).where(
                EvalMetric.case_result_id == case_result_id,
                EvalMetric.run_id.is_(None),
                EvalMetric.metric_key == metric_key,
                EvalMetric.metric_version == metric_version,
            )
        )
        return result.scalar_one_or_none()

    async def create_dataset(self, item: EvalDatasetCreate) -> EvalDataset:
        dataset = EvalDataset(
            dataset_key=item.dataset_key,
            dataset_version=item.dataset_version,
            display_name=item.display_name,
            dataset_status=item.dataset_status,
            schema_set_id=item.schema_set_id,
            schema_set_version=item.schema_set_version,
            schema_set_sha256=item.schema_set_sha256,
            manifest_hash=item.manifest_hash,
            manifest_uri=item.manifest_uri,
            source_classification=item.source_classification,
            case_count=item.case_count,
            created_by=item.created_by,
            reviewed_by=item.reviewed_by,
            approved_by=item.approved_by,
        )
        self.session.add(dataset)
        await self.session.flush()
        return dataset

    async def create_case(self, item: EvalCaseCreate) -> EvalCase:
        eval_case = EvalCase(
            dataset_id=item.dataset_id,
            case_key=item.case_key,
            case_version=item.case_version,
            partition=item.partition,
            experiment_type=item.experiment_type,
            input_hash=item.input_hash,
            gold_hash=item.gold_hash,
            question_template=item.question_template,
            source_segment=item.source_segment,
            medication_family=item.medication_family,
            transform_origin=item.transform_origin,
            expected_scope_codes=item.expected_scope_codes,
            expected_outcome_ref=item.expected_outcome_ref,
        )
        self.session.add(eval_case)
        await self.session.flush()
        return eval_case

    async def create_experiment(self, item: EvalExperimentCreate) -> EvalExperiment:
        experiment = EvalExperiment(
            dataset_id=item.dataset_id,
            experiment_key=item.experiment_key,
            experiment_version=item.experiment_version,
            experiment_type=item.experiment_type,
            policy_ref=item.policy_ref,
            policy_hash=item.policy_hash,
            metric_set_ref=item.metric_set_ref,
            rubric_ref=item.rubric_ref,
            is_release_gate=item.is_release_gate,
        )
        self.session.add(experiment)
        await self.session.flush()
        return experiment

    async def create_variant(self, item: EvalVariantCreate) -> EvalVariant:
        variant = EvalVariant(
            experiment_id=item.experiment_id,
            variant_key=item.variant_key,
            variant_role=item.variant_role,
            config_hash=item.config_hash,
            runtime_bundle_ref=item.runtime_bundle_ref,
            source_snapshot_ref=item.source_snapshot_ref,
            candidate_index_ref=item.candidate_index_ref,
            knowledge_index_ref=item.knowledge_index_ref,
            model_ref=item.model_ref,
            prompt_ref=item.prompt_ref,
            parser_ref=item.parser_ref,
            embedding_ref=item.embedding_ref,
        )
        self.session.add(variant)
        await self.session.flush()
        return variant

    async def create_run(self, item: EvalRunCreate) -> EvalRun:
        _validate_decision_status_matches_execution(
            execution_status=item.execution_status,
            decision_status=item.decision_status,
        )
        run = EvalRun(
            run_key=item.run_key,
            dataset_id=item.dataset_id,
            experiment_id=item.experiment_id,
            variant_id=item.variant_id,
            experiment_type=item.experiment_type,
            execution_status=item.execution_status,
            decision_status=item.decision_status,
            git_commit_sha=item.git_commit_sha,
            dataset_manifest_hash=item.dataset_manifest_hash,
            execution_manifest_hash=item.execution_manifest_hash,
            candidate_guard_ref=item.candidate_guard_ref,
            blocking_execution_statuses=item.blocking_execution_statuses,
            started_at=item.started_at,
            completed_at=item.completed_at,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def create_case_result(self, item: EvalCaseResultCreate) -> EvalCaseResult:
        _validate_decision_status_matches_execution(
            execution_status=item.execution_status,
            decision_status=item.decision_status,
        )
        case_result = EvalCaseResult(
            run_id=item.run_id,
            case_id=item.case_id,
            dataset_id=item.dataset_id,
            experiment_type=item.experiment_type,
            execution_status=item.execution_status,
            decision_status=item.decision_status,
            request_guard_ref=item.request_guard_ref,
            result_summary_hash=item.result_summary_hash,
            non_sensitive_summary=item.non_sensitive_summary,
        )
        self.session.add(case_result)
        await self.session.flush()
        return case_result

    async def create_metric(self, item: EvalMetricCreate) -> EvalMetric:
        metric = EvalMetric(
            run_id=item.run_id,
            case_result_id=item.case_result_id,
            metric_scope=item.metric_scope,
            metric_key=item.metric_key,
            metric_version=item.metric_version,
            metric_area=item.metric_area,
            numerator=item.numerator,
            denominator=item.denominator,
            score=item.score,
            confidence_lower=item.confidence_lower,
            confidence_upper=item.confidence_upper,
            is_release_blocking=item.is_release_blocking,
            is_diagnostic=item.is_diagnostic,
        )
        self.session.add(metric)
        await self.session.flush()
        return metric

    async def create_failure(self, item: EvalFailureCreate) -> EvalFailure:
        failure = EvalFailure(
            run_id=item.run_id,
            case_result_id=item.case_result_id,
            failure_scope=item.failure_scope,
            failure_code=item.failure_code,
            failure_area=item.failure_area,
            context_hash=item.context_hash,
            non_sensitive_context=item.non_sensitive_context,
        )
        self.session.add(failure)
        await self.session.flush()
        return failure
