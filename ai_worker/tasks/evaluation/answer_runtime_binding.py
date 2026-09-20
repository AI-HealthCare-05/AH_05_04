"""Approved Phase B Answer Runtime Authority Binding recipes and projections."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from pydantic import TypeAdapter

from ai_worker.tasks.evaluation.answer_comparison import (
    AnswerComparisonDeltaBindings,
    AnswerComparisonSupplementalControls,
)
from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.comparison import LoadedRunBundle
from ai_worker.tasks.evaluation.config import ActualRetrievalModelConfig, DevExecutionRequest
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerRuntimeAuthorityBindingManifest,
    AnswerVariantId,
    answer_runtime_not_applied_binding_hash,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult, RagEvaluationRun
from ai_worker.tasks.evaluation.schemas.common import (
    MAX_SAFE_INTEGER,
    CanonicalUuid,
    ExecutionStatus,
    ExperimentType,
)
from ai_worker.tasks.rag.evidence_retrieval import is_valid_immutable_artifact_ref
from ai_worker.tasks.rag.evidence_search import VersionedEvidenceRetrievalConfiguration
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationProvenance
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingObservation

_RUN_ID_ADAPTER = TypeAdapter(CanonicalUuid)


def _invalid() -> EvaluationValidationError:
    return EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


def compute_answer_runtime_binding_manifest_sha256(payload: Mapping[str, JsonValue]) -> str:
    return canonical_sha256(
        cast(JsonValue, dict(payload)),
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def compute_not_applied_binding_hash(axis: str) -> str:
    try:
        return answer_runtime_not_applied_binding_hash(axis)
    except ValueError:
        raise _invalid() from None


def compute_input_context_binding_hash_from_cases(cases: Sequence[CaseResult]) -> str:
    sorted_cases = sorted(cases, key=lambda case: case.case_id.encode("utf-16-be"))
    return canonical_sha256(
        {
            "cases": [{"case_id": case.case_id, "input_sha256": case.input_sha256} for case in sorted_cases],
            "projection_version": "answer-input-context-v1",
        }
    )


def compute_input_context_binding_hash(bundle: LoadedRunBundle) -> str:
    return compute_input_context_binding_hash_from_cases(bundle.cases)


def compute_prompt_structure_binding_hash(provenance: GuidelineGenerationProvenance) -> str:
    if (
        type(provenance) is not GuidelineGenerationProvenance
        or not is_valid_immutable_artifact_ref(provenance.prompt_ref)
        or provenance.prompt_ref.artifact_code != "guideline-prompt"
    ):
        raise _invalid()
    return provenance.prompt_ref.content_sha256


def compute_parser_binding_hash(provenance: GuidelineGenerationProvenance) -> str:
    if (
        type(provenance) is not GuidelineGenerationProvenance
        or not is_valid_immutable_artifact_ref(provenance.parser_ref)
        or provenance.parser_ref.artifact_code != "guideline-parser"
    ):
        raise _invalid()
    return provenance.parser_ref.content_sha256


def compute_seed_binding_hash(request: DevExecutionRequest) -> str:
    if type(request) is not DevExecutionRequest:
        raise _invalid()
    return canonical_sha256({"projection_version": "answer-seed-v1", "seed": request.seed})


def compute_sampling_parameters_binding_hash() -> str:
    return canonical_sha256(
        {
            "projection_version": "answer-sampling-parameters-v1",
            "temperature": "0",
        }
    )


def compute_token_limit_binding_hash(max_output_tokens: int) -> str:
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_SAFE_INTEGER:
        raise _invalid()
    return canonical_sha256(
        {
            "max_output_tokens": max_output_tokens,
            "projection_version": "answer-token-limit-v1",
        }
    )


def compute_timeout_binding_hash(timeout_seconds: int | float | Decimal) -> str:
    if type(timeout_seconds) not in (int, float, Decimal):
        raise _invalid()
    try:
        canonical_timeout = Decimal(str(timeout_seconds))
    except Exception:
        raise _invalid() from None
    if not canonical_timeout.is_finite() or canonical_timeout <= 0:
        raise _invalid()
    return canonical_sha256(
        {
            "projection_version": "answer-timeout-v1",
            "timeout_seconds": str(canonical_timeout),
        }
    )


def compute_retrieval_pipeline_binding_hash(
    variant_id: AnswerVariantId,
    retrieval_config: VersionedEvidenceRetrievalConfiguration | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if retrieval_config is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("RETRIEVAL_PIPELINE")
    if variant_id is AnswerVariantId.ANS_RAG:
        if (
            type(retrieval_config) is not VersionedEvidenceRetrievalConfiguration
            or not retrieval_config.is_hash_valid()
        ):
            raise _invalid()
        return retrieval_config.artifact_ref.content_sha256
    if variant_id is AnswerVariantId.ANS_FINAL:
        if (
            type(retrieval_config) is not VersionedEvidenceRetrievalConfiguration
            or not retrieval_config.is_hash_valid()
        ):
            raise _invalid()
        return retrieval_config.artifact_ref.content_sha256
    raise _invalid()


def compute_source_index_binding_hash(
    variant_id: AnswerVariantId,
    model_config: ActualRetrievalModelConfig | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if model_config is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("SOURCE_INDEX")
    if variant_id is AnswerVariantId.ANS_RAG:
        if type(model_config) is not ActualRetrievalModelConfig:
            raise _invalid()
        return model_config.knowledge_index_ref.hash
    if variant_id is AnswerVariantId.ANS_FINAL:
        if type(model_config) is not ActualRetrievalModelConfig:
            raise _invalid()
        return model_config.knowledge_index_ref.hash
    raise _invalid()


def compute_runtime_bundle_binding_hash(
    variant_id: AnswerVariantId,
    observation: RequestGuardRuntimeBindingObservation | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if observation is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("RUNTIME_BUNDLE")
    if variant_id is AnswerVariantId.ANS_RAG:
        if type(observation) is not RequestGuardRuntimeBindingObservation:
            raise _invalid()
        return canonical_sha256(
            {
                "bundle_id": str(observation.bundle_id),
                "bundle_manifest_hash": observation.bundle_manifest_hash,
                "environment": observation.environment.value,
                "projection_version": "answer-runtime-bundle-binding-v1",
            }
        )
    if variant_id is AnswerVariantId.ANS_FINAL:
        if type(observation) is not RequestGuardRuntimeBindingObservation:
            raise _invalid()
        return canonical_sha256(
            {
                "bundle_id": str(observation.bundle_id),
                "bundle_manifest_hash": observation.bundle_manifest_hash,
                "environment": observation.environment.value,
                "projection_version": "answer-runtime-bundle-binding-v1",
            }
        )
    raise _invalid()


def validate_answer_runtime_binding_manifest_for_run(
    manifest: AnswerRuntimeAuthorityBindingManifest,
    run: RagEvaluationRun,
    expected_variant: AnswerVariantId,
) -> None:
    if (
        type(expected_variant) is not AnswerVariantId
        or manifest.experiment_id != run.experiment_id
        or manifest.run_id != run.run_id
        or manifest.variant_id is not expected_variant
        or run.variant_id != expected_variant.value
    ):
        raise _invalid()


def project_answer_runtime_binding_manifest(
    manifest: AnswerRuntimeAuthorityBindingManifest,
) -> tuple[AnswerComparisonSupplementalControls, AnswerComparisonDeltaBindings]:
    supplemental = manifest.supplemental_controls
    delta = manifest.delta_bindings
    return (
        AnswerComparisonSupplementalControls(
            input_context_hash=supplemental.input_context_hash,
            prompt_structure_hash=supplemental.prompt_structure_hash,
            parser_hash=supplemental.parser_hash,
            seed_hash=supplemental.seed_hash,
            sampling_parameters_hash=supplemental.sampling_parameters_hash,
            token_limit_hash=supplemental.token_limit_hash,
            timeout_hash=supplemental.timeout_hash,
        ),
        AnswerComparisonDeltaBindings(
            retrieval_pipeline_hash=delta.retrieval_pipeline_hash,
            source_index_hash=delta.source_index_hash,
            runtime_bundle_hash=delta.runtime_bundle_hash,
            retrieved_evidence_hash=delta.retrieved_evidence_hash,
            final_validator_hash=delta.final_validator_hash,
            citation_gate_hash=delta.citation_gate_hash,
            safety_gate_hash=delta.safety_gate_hash,
            release_gate_hash=delta.release_gate_hash,
        ),
    )


@dataclass(frozen=True, slots=True)
class ProviderInvocationObservation:
    case_id: str
    run_id: str
    variant_id: str
    temperature: str
    max_output_tokens: int
    timeout_seconds: Decimal

    def __post_init__(self) -> None:
        if not self.case_id or not self.case_id.strip():
            raise _invalid()
        try:
            _RUN_ID_ADAPTER.validate_python(self.run_id)
        except Exception:
            raise _invalid() from None
        try:
            variant = AnswerVariantId(self.variant_id)
        except Exception:
            raise _invalid() from None
        if self.variant_id != variant.value:
            raise _invalid()
        if type(self.temperature) is not str or self.temperature != "0":
            raise _invalid()
        if (
            type(self.max_output_tokens) is not int
            or type(self.max_output_tokens) is bool
            or not 1 <= self.max_output_tokens <= MAX_SAFE_INTEGER
        ):
            raise _invalid()
        if (
            type(self.timeout_seconds) is not Decimal
            or not self.timeout_seconds.is_finite()
            or self.timeout_seconds <= 0
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class AggregatedProviderInvocation:
    temperature: str
    max_output_tokens: int
    timeout_seconds: Decimal


def _parse_timeout_parameter(raw_timeout: object) -> Decimal:
    if type(raw_timeout) is bool or type(raw_timeout) not in (int, float, Decimal):
        raise _invalid()
    try:
        canonical_timeout = Decimal(str(raw_timeout))
    except Exception:
        raise _invalid() from None
    if not canonical_timeout.is_finite() or canonical_timeout <= 0:
        raise _invalid()
    return canonical_timeout


def _extract_and_validate_answer_parameters(
    execution_request: DevExecutionRequest,
    variant_id: AnswerVariantId,
) -> tuple[int, Decimal]:
    if (
        type(execution_request) is not DevExecutionRequest
        or type(variant_id) is not AnswerVariantId
        or execution_request.experiment_type
        not in (
            ExperimentType.ANSWER_GROUNDING_SAFETY,
            ExperimentType.END_TO_END_RAG,
        )
        or execution_request.variant_id != variant_id.value
        or execution_request.answer_variant is None
    ):
        raise _invalid()

    answer_variant = execution_request.answer_variant
    if answer_variant.variant_id != variant_id.value or answer_variant.kind != "ANSWER":
        raise _invalid()
    if "token_limit" not in answer_variant.parameters or "timeout" not in answer_variant.parameters:
        raise _invalid()

    raw_token_limit = answer_variant.parameters["token_limit"]
    if (
        type(raw_token_limit) is not int
        or type(raw_token_limit) is bool
        or not 1 <= raw_token_limit <= MAX_SAFE_INTEGER
    ):
        raise _invalid()

    return raw_token_limit, _parse_timeout_parameter(answer_variant.parameters["timeout"])


def _validate_required_case_ids(required_case_ids: Sequence[str]) -> None:
    if not required_case_ids or len(required_case_ids) != len(set(required_case_ids)):
        raise _invalid()
    for case_id in required_case_ids:
        if not case_id or not str(case_id).strip():
            raise _invalid()


def _validate_provider_observation_collection(
    run_id: str,
    variant_id: AnswerVariantId,
    required_case_ids: Sequence[str],
    observations: Sequence[ProviderInvocationObservation],
) -> AggregatedProviderInvocation:
    try:
        _RUN_ID_ADAPTER.validate_python(run_id)
    except Exception:
        raise _invalid() from None

    if type(variant_id) is not AnswerVariantId:
        raise _invalid()

    _validate_required_case_ids(required_case_ids)

    if not observations:
        raise _invalid()
    observed_case_ids = [obs.case_id for obs in observations]
    if len(observed_case_ids) != len(set(observed_case_ids)) or set(observed_case_ids) != set(required_case_ids):
        raise _invalid()

    for obs in observations:
        if obs.run_id != run_id or obs.variant_id != variant_id.value:
            raise _invalid()

    temperatures = {obs.temperature for obs in observations}
    token_limits = {obs.max_output_tokens for obs in observations}
    timeouts = {obs.timeout_seconds for obs in observations}
    if len(temperatures) != 1 or len(token_limits) != 1 or len(timeouts) != 1:
        raise _invalid()

    return AggregatedProviderInvocation(
        temperature=next(iter(temperatures)),
        max_output_tokens=next(iter(token_limits)),
        timeout_seconds=next(iter(timeouts)),
    )


def validate_and_aggregate_provider_observations(
    run_id: str,
    variant_id: AnswerVariantId,
    required_case_ids: Sequence[str],
    observations: Sequence[ProviderInvocationObservation],
    execution_request: DevExecutionRequest,
) -> AggregatedProviderInvocation:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    try:
        _RUN_ID_ADAPTER.validate_python(run_id)
    except Exception:
        raise _invalid() from None

    expected_tokens, expected_timeout = _extract_and_validate_answer_parameters(execution_request, variant_id)
    aggregated = _validate_provider_observation_collection(run_id, variant_id, required_case_ids, observations)

    if (
        aggregated.temperature != "0"
        or aggregated.max_output_tokens != expected_tokens
        or aggregated.timeout_seconds != expected_timeout
    ):
        raise _invalid()

    return aggregated


@dataclass(frozen=True, slots=True)
class AnswerRuntimeBindingMaterializationInput:
    experiment_id: str
    run_id: str
    variant_id: AnswerVariantId
    execution_request: DevExecutionRequest
    guideline_provenance: GuidelineGenerationProvenance
    provider_observations: tuple[ProviderInvocationObservation, ...]
    required_case_ids: tuple[str, ...]
    cases: tuple[CaseResult, ...]


@dataclass(frozen=True, slots=True)
class MaterializedAnswerRuntimeSupplementalBindings:
    input_context_hash: str
    seed_hash: str
    prompt_structure_hash: str
    parser_hash: str
    sampling_parameters_hash: str
    token_limit_hash: str
    timeout_hash: str


def materialize_answer_runtime_supplemental_bindings(
    input_data: AnswerRuntimeBindingMaterializationInput,
) -> MaterializedAnswerRuntimeSupplementalBindings:
    if (
        type(input_data) is not AnswerRuntimeBindingMaterializationInput
        or type(input_data.variant_id) is not AnswerVariantId
        or input_data.experiment_id != input_data.execution_request.experiment_id
    ):
        raise _invalid()

    try:
        _RUN_ID_ADAPTER.validate_python(input_data.run_id)
    except Exception:
        raise _invalid() from None

    aggregated = validate_and_aggregate_provider_observations(
        run_id=input_data.run_id,
        variant_id=input_data.variant_id,
        required_case_ids=input_data.required_case_ids,
        observations=input_data.provider_observations,
        execution_request=input_data.execution_request,
    )

    if not input_data.cases:
        raise _invalid()
    case_ids = [case.case_id for case in input_data.cases]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != set(input_data.required_case_ids):
        raise _invalid()

    for case in input_data.cases:
        if case.run_id != input_data.run_id:
            raise _invalid()
        if case.execution_status is not ExecutionStatus.COMPLETED:
            raise _invalid()

    return MaterializedAnswerRuntimeSupplementalBindings(
        input_context_hash=compute_input_context_binding_hash_from_cases(input_data.cases),
        seed_hash=compute_seed_binding_hash(input_data.execution_request),
        prompt_structure_hash=compute_prompt_structure_binding_hash(input_data.guideline_provenance),
        parser_hash=compute_parser_binding_hash(input_data.guideline_provenance),
        sampling_parameters_hash=compute_sampling_parameters_binding_hash(),
        token_limit_hash=compute_token_limit_binding_hash(aggregated.max_output_tokens),
        timeout_hash=compute_timeout_binding_hash(aggregated.timeout_seconds),
    )
