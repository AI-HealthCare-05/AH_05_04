"""Phase B tests for the Answer Runtime Authority Binding Manifest (#159)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

from ai_worker.tasks.evaluation.actual_retrieval import sealed_retrieval_config
from ai_worker.tasks.evaluation.answer_comparison import (
    AnswerComparisonDeltaBindings,
    AnswerComparisonSupplementalControls,
    build_answer_pair_comparison,
)
from ai_worker.tasks.evaluation.answer_runtime_binding import (
    compute_answer_runtime_binding_manifest_sha256,
    compute_input_context_binding_hash,
    compute_not_applied_binding_hash,
    compute_parser_binding_hash,
    compute_prompt_structure_binding_hash,
    compute_retrieval_pipeline_binding_hash,
    compute_runtime_bundle_binding_hash,
    compute_sampling_parameters_binding_hash,
    compute_seed_binding_hash,
    compute_source_index_binding_hash,
    compute_timeout_binding_hash,
    compute_token_limit_binding_hash,
    project_answer_runtime_binding_manifest,
    validate_answer_runtime_binding_manifest_for_run,
)
from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import ActualRetrievalModelConfig, DevExecutionRequest
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerRuntimeAuthorityBindingManifest,
    AnswerVariantId,
    parse_answer_runtime_binding_manifest_bytes,
)
from ai_worker.tasks.evaluation.schemas.artifacts import ExecutionStatus
from ai_worker.tasks.evaluation.schemas.common import ExperimentType, ImmutableReference
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationProvenance
from ai_worker.tests.evaluation.test_answer_comparison import (
    _default_delta,
    _make_bundle,
    _make_cases,
    _make_run,
    _make_run_input,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    canonical_scope_manifest_hash,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

_DEFAULT_BUNDLE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_DEFAULT_REQUEST_GUARD_DECISION_ID = UUID("11111111-1111-4111-8111-111111111111")
_DEFAULT_USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def _manifest_payload(
    *,
    run_id: str = "11111111-1111-4111-8111-111111111111",
    experiment_id: str = "exp-answer-dev-1",
    variant_id: str = "ANS-RAG",
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.answer-runtime-binding-manifest",
        "schema_version": "1.0.0",
        "experiment_id": experiment_id,
        "run_id": run_id,
        "variant_id": variant_id,
        "supplemental_controls": {
            "input_context_hash": "1" * 64,
            "prompt_structure_hash": "2" * 64,
            "parser_hash": "3" * 64,
            "seed_hash": "4" * 64,
            "sampling_parameters_hash": "5" * 64,
            "token_limit_hash": "6" * 64,
            "timeout_hash": "7" * 64,
        },
        "delta_bindings": {
            "retrieval_pipeline_hash": "8" * 64,
            "source_index_hash": "9" * 64,
            "runtime_bundle_hash": "a" * 64,
            "retrieved_evidence_hash": None,
            "final_validator_hash": None,
            "citation_gate_hash": None,
            "safety_gate_hash": None,
            "release_gate_hash": None,
        },
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    return payload


def _rehash(payload: dict[str, JsonValue]) -> None:
    payload["manifest_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def _parse(payload: dict[str, JsonValue]) -> AnswerRuntimeAuthorityBindingManifest:
    return parse_answer_runtime_binding_manifest_bytes(canonical_json_bytes(payload))


def _provenance() -> GuidelineGenerationProvenance:
    return GuidelineGenerationProvenance(
        prompt_ref=ImmutableArtifactRef("guideline-prompt", "1.0.0", "1" * 64),
        model_ref=ImmutableArtifactRef("guideline-model", "1.0.0", "2" * 64),
        parser_ref=ImmutableArtifactRef("guideline-parser", "1.0.0", "3" * 64),
        validator_ref=ImmutableArtifactRef("guideline-validator", "1.0.0", "4" * 64),
    )


def _execution_request(seed: int) -> DevExecutionRequest:
    return DevExecutionRequest(
        config_id="answer-dev",
        config_version="1.0.0",
        experiment_id="exp-answer-dev-1",
        experiment_type=ExperimentType.ANSWER_GROUNDING_SAFETY,
        variant_id="ANS-RAG",
        evaluated_partitions=("DEV",),
        environment="LOCAL",
        dataset_manifest_path="evals/dataset.json",
        profile_path="evals/profile.json",
        comparison_policy_path="evals/comparison.json",
        evaluation_policy_path="evals/evaluation.json",
        suite_path="evals/suite.json",
        upstream_contract_manifest_hash="a" * 64,
        retrieval_variant=None,
        answer_variant=None,
        seed=seed,
        retry_policy="NO_AUTOMATIC_RETRY",
        max_attempts=1,
    )


def _retrieval_config() -> VersionedEvidenceRetrievalConfiguration:
    return sealed_retrieval_config(
        VersionedEvidenceRetrievalConfiguration(
            artifact_ref=ImmutableArtifactRef("retrieval-config", "1.0.0", "0" * 64),
            execution_mode=RetrievalExecutionMode.HYBRID_RRF,
            lexical_config=VersionedLexicalSearchConfiguration(
                artifact_ref=ImmutableArtifactRef("lexical-config", "1.0.0", "0" * 64)
            ),
            dense_config=VersionedDenseSearchConfiguration(
                artifact_ref=ImmutableArtifactRef("dense-config", "1.0.0", "0" * 64)
            ),
            expected_query_embedding_adapter_ref=ImmutableArtifactRef("embedding-adapter", "1.0.0", "e" * 64),
        )
    )


def _retrieval_model_config(index_hash: str = "b" * 64) -> ActualRetrievalModelConfig:
    return ActualRetrievalModelConfig(
        adapter_id="actual-retrieval.v1",
        provider_invocation=False,
        source_snapshot_ref=ImmutableReference(id="source", version="1.0.0", hash="1" * 64),
        knowledge_index_ref=ImmutableReference(id="index", version="1.0.0", hash=index_hash),
        embedding_model_ref=ImmutableReference(id="embedding", version="1.0.0", hash="2" * 64),
        parser_ref=ImmutableReference(id="parser", version="1.0.0", hash="3" * 64),
        filter_snapshot_hash="4" * 64,
    )


def _observation(
    *,
    environment: RuntimeEnvironmentCode = RuntimeEnvironmentCode.LOCAL,
    bundle_id: UUID = _DEFAULT_BUNDLE_ID,
    bundle_manifest_hash: str = "b" * 64,
    request_guard_decision_id: UUID = _DEFAULT_REQUEST_GUARD_DECISION_ID,
    user_id: UUID = _DEFAULT_USER_ID,
    request_operation_code: str = "GUIDE_GENERATE",
    request_scope_codes: tuple[str, ...] = ("guide:read",),
) -> RequestGuardRuntimeBindingObservation:
    return RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=request_guard_decision_id,
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
        user_id=user_id,
        request_operation_code=request_operation_code,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        environment=environment,
        bundle_id=bundle_id,
        bundle_manifest_hash=bundle_manifest_hash,
        request_scope_codes=request_scope_codes,
        scope_manifest_hash=canonical_scope_manifest_hash(request_scope_codes),
        legacy_request_authority_ref=RequestAuthorityArtifactRef(
            "request_guard_authority",
            "1.0",
            "c" * 64,
        ),
    )


def test_manifest_parser_accepts_valid_manifest_and_nullable_deltas() -> None:
    manifest = _parse(_manifest_payload())

    assert manifest.variant_id is AnswerVariantId.ANS_RAG
    assert manifest.delta_bindings.retrieved_evidence_hash is None
    assert manifest.delta_bindings.release_gate_hash is None


@pytest.mark.parametrize(
    "case",
    (
        "extra_top",
        "extra_supplemental",
        "extra_delta",
        "invalid_uuid",
        "invalid_sha",
        "supplemental_none",
        "wrong_schema_id",
        "wrong_schema_version",
    ),
)
def test_manifest_parser_rejects_invalid_contract_shapes(case: str) -> None:
    payload = _manifest_payload()
    supplemental = payload["supplemental_controls"]
    delta = payload["delta_bindings"]
    assert isinstance(supplemental, dict)
    assert isinstance(delta, dict)
    if case == "extra_top":
        payload["unexpected"] = "x"
    elif case == "extra_supplemental":
        supplemental["unexpected"] = "x"
    elif case == "extra_delta":
        delta["unexpected"] = "x"
    elif case == "invalid_uuid":
        payload["run_id"] = "not-a-uuid"
    elif case == "invalid_sha":
        supplemental["seed_hash"] = "bad"
    elif case == "supplemental_none":
        supplemental["seed_hash"] = None
    elif case == "wrong_schema_id":
        payload["schema_id"] = "rag-eval.wrong"
    else:
        payload["schema_version"] = "2.0.0"
    _rehash(payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        _parse(payload)

    assert exc_info.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_manifest_parser_reports_self_hash_mismatch() -> None:
    payload = _manifest_payload()
    payload["experiment_id"] = "exp-tampered"

    with pytest.raises(EvaluationValidationError) as exc_info:
        _parse(payload)

    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_manifest_hash_is_deterministic_and_run_scoped() -> None:
    first = _manifest_payload()
    same = deepcopy(first)
    other_run = _manifest_payload(run_id="22222222-2222-4222-8222-222222222222")

    assert compute_answer_runtime_binding_manifest_sha256(first) == first["manifest_sha256"]
    assert compute_answer_runtime_binding_manifest_sha256(same) == first["manifest_sha256"]
    assert other_run["manifest_sha256"] != first["manifest_sha256"]


def test_not_applied_hash_is_axis_scoped_and_rejects_unknown_axis() -> None:
    axes = (
        "RETRIEVAL_PIPELINE",
        "SOURCE_INDEX",
        "RUNTIME_BUNDLE",
        "RETRIEVED_EVIDENCE",
        "FINAL_VALIDATOR",
        "CITATION_GATE",
        "SAFETY_GATE",
        "RELEASE_GATE",
    )
    hashes = {axis: compute_not_applied_binding_hash(axis) for axis in axes}

    assert len(set(hashes.values())) == 8
    assert compute_not_applied_binding_hash("SOURCE_INDEX") == hashes["SOURCE_INDEX"]
    assert hashes["SOURCE_INDEX"] == canonical_sha256(
        {
            "axis": "SOURCE_INDEX",
            "binding_state": "NOT_APPLIED",
            "projection_version": "answer-authority-binding-v1",
        }
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        compute_not_applied_binding_hash("UNKNOWN")
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_input_context_hash_is_order_independent_and_uses_only_case_binding() -> None:
    base_bundle = _make_bundle(
        AnswerVariantId.ANS_RAG,
        run_id="11111111-1111-4111-8111-111111111111",
    )
    case_a = _make_cases(
        base_bundle.run.run_id,
        case_id="case-a",
        input_sha256="a" * 64,
    )[0]
    case_b = _make_cases(
        base_bundle.run.run_id,
        case_id="case-b",
        input_sha256="b" * 64,
    )[0]
    bundle_ab = replace(base_bundle, cases=(case_a, case_b))
    bundle_ba = replace(base_bundle, cases=(case_b, case_a))
    expected = canonical_sha256(
        {
            "cases": [
                {"case_id": "case-a", "input_sha256": "a" * 64},
                {"case_id": "case-b", "input_sha256": "b" * 64},
            ],
            "projection_version": "answer-input-context-v1",
        }
    )

    assert compute_input_context_binding_hash(bundle_ba) == expected
    assert compute_input_context_binding_hash(bundle_ab) == expected
    assert (
        compute_input_context_binding_hash(
            replace(
                base_bundle,
                cases=(
                    _make_cases(
                        base_bundle.run.run_id,
                        case_id="case-c",
                        input_sha256="a" * 64,
                    )[0],
                    case_b,
                ),
            )
        )
        != expected
    )
    assert (
        compute_input_context_binding_hash(
            replace(
                base_bundle,
                cases=(
                    _make_cases(
                        base_bundle.run.run_id,
                        case_id="case-a",
                        input_sha256="c" * 64,
                    )[0],
                    case_b,
                ),
            )
        )
        != expected
    )


def test_prompt_and_parser_recipes_reuse_immutable_hashes_without_rehashing() -> None:
    provenance = _provenance()

    assert compute_prompt_structure_binding_hash(provenance) == "1" * 64
    assert compute_parser_binding_hash(provenance) == "3" * 64


def test_seed_sampling_token_and_timeout_recipes_use_exact_approved_projections() -> None:
    assert compute_seed_binding_hash(_execution_request(7)) == canonical_sha256(
        {"projection_version": "answer-seed-v1", "seed": 7}
    )
    assert compute_seed_binding_hash(_execution_request(7)) != compute_seed_binding_hash(_execution_request(8))
    assert compute_sampling_parameters_binding_hash() == canonical_sha256(
        {"projection_version": "answer-sampling-parameters-v1", "temperature": "0"}
    )
    assert compute_sampling_parameters_binding_hash() != canonical_sha256(
        {
            "frequency_penalty": "0",
            "presence_penalty": "0",
            "projection_version": "answer-sampling-parameters-v1",
            "temperature": "0",
            "top_p": "1",
        }
    )
    assert compute_token_limit_binding_hash(2048) == canonical_sha256(
        {"max_output_tokens": 2048, "projection_version": "answer-token-limit-v1"}
    )
    assert compute_token_limit_binding_hash(2048) != compute_token_limit_binding_hash(4096)
    assert compute_timeout_binding_hash(30.5) == canonical_sha256(
        {"projection_version": "answer-timeout-v1", "timeout_seconds": "30.5"}
    )
    assert compute_timeout_binding_hash(Decimal("30.5")) == compute_timeout_binding_hash(30.5)
    assert compute_timeout_binding_hash(30.5) != compute_timeout_binding_hash(31)


@pytest.mark.parametrize("invalid", [True, 0, -1, 2**53])
def test_token_limit_recipe_rejects_invalid_values(invalid: object) -> None:
    with pytest.raises(EvaluationValidationError):
        compute_token_limit_binding_hash(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [True, 0, -1, float("nan"), float("inf")])
def test_timeout_recipe_rejects_invalid_values(invalid: object) -> None:
    with pytest.raises(EvaluationValidationError):
        compute_timeout_binding_hash(invalid)  # type: ignore[arg-type]


def test_retrieval_pipeline_and_source_index_reuse_authoritative_hashes() -> None:
    retrieval_config = _retrieval_config()
    model_config = _retrieval_model_config()

    assert (
        compute_retrieval_pipeline_binding_hash(AnswerVariantId.ANS_RAG, retrieval_config)
        == retrieval_config.artifact_ref.content_sha256
    )
    assert compute_source_index_binding_hash(AnswerVariantId.ANS_FINAL, model_config) == "b" * 64
    assert compute_retrieval_pipeline_binding_hash(AnswerVariantId.ANS_BASE) == compute_not_applied_binding_hash(
        "RETRIEVAL_PIPELINE"
    )
    assert compute_source_index_binding_hash(AnswerVariantId.ANS_BASE) == compute_not_applied_binding_hash(
        "SOURCE_INDEX"
    )


def test_retrieval_pipeline_rejects_invalid_configuration_hash() -> None:
    config = replace(
        _retrieval_config(),
        artifact_ref=ImmutableArtifactRef("retrieval-config", "1.0.0", "f" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        compute_retrieval_pipeline_binding_hash(AnswerVariantId.ANS_RAG, config)
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_runtime_bundle_hash_excludes_request_specific_metadata() -> None:
    changed_metadata = _observation(
        request_guard_decision_id=UUID("33333333-3333-4333-8333-333333333333"),
        user_id=UUID("44444444-4444-4444-8444-444444444444"),
        request_operation_code="GUIDE_RETRY",
        request_scope_codes=("guide:read", "guide:retry"),
    )
    expected = canonical_sha256(
        {
            "bundle_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bundle_manifest_hash": "b" * 64,
            "environment": "LOCAL",
            "projection_version": "answer-runtime-bundle-binding-v1",
        }
    )

    assert compute_runtime_bundle_binding_hash(AnswerVariantId.ANS_RAG, _observation()) == expected
    assert compute_runtime_bundle_binding_hash(AnswerVariantId.ANS_FINAL, changed_metadata) == expected
    assert compute_runtime_bundle_binding_hash(AnswerVariantId.ANS_BASE) == compute_not_applied_binding_hash(
        "RUNTIME_BUNDLE"
    )


@pytest.mark.parametrize(
    "changed",
    [
        _observation(environment=RuntimeEnvironmentCode.TEST),
        _observation(bundle_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")),
        _observation(bundle_manifest_hash="d" * 64),
    ],
)
def test_runtime_bundle_hash_changes_with_canonical_binding_fields(
    changed: RequestGuardRuntimeBindingObservation,
) -> None:
    assert compute_runtime_bundle_binding_hash(AnswerVariantId.ANS_RAG, changed) != compute_runtime_bundle_binding_hash(
        AnswerVariantId.ANS_RAG,
        _observation(),
    )


def test_manifest_projects_exactly_to_808_typed_seam_and_preserves_none() -> None:
    manifest = _parse(_manifest_payload())

    supplemental, delta = project_answer_runtime_binding_manifest(manifest)

    assert supplemental == AnswerComparisonSupplementalControls(
        input_context_hash="1" * 64,
        prompt_structure_hash="2" * 64,
        parser_hash="3" * 64,
        seed_hash="4" * 64,
        sampling_parameters_hash="5" * 64,
        token_limit_hash="6" * 64,
        timeout_hash="7" * 64,
    )
    assert delta == AnswerComparisonDeltaBindings(
        retrieval_pipeline_hash="8" * 64,
        source_index_hash="9" * 64,
        runtime_bundle_hash="a" * 64,
        retrieved_evidence_hash=None,
        final_validator_hash=None,
        citation_gate_hash=None,
        safety_gate_hash=None,
        release_gate_hash=None,
    )


@pytest.mark.parametrize(
    ("manifest_field", "manifest_value"),
    [
        ("run_id", "22222222-2222-4222-8222-222222222222"),
        ("experiment_id", "other-experiment"),
        ("variant_id", "ANS-FINAL"),
    ],
)
def test_run_binding_rejects_wrong_identity(manifest_field: str, manifest_value: str) -> None:
    payload = _manifest_payload()
    payload[manifest_field] = manifest_value
    _rehash(payload)
    manifest = _parse(payload)
    run = _make_run(
        AnswerVariantId.ANS_RAG,
        run_id="11111111-1111-4111-8111-111111111111",
        experiment_id="exp-answer-dev-1",
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_runtime_binding_manifest_for_run(manifest, run, AnswerVariantId.ANS_RAG)
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_run_binding_accepts_exact_identity() -> None:
    manifest = _parse(_manifest_payload())
    run = _make_run(
        AnswerVariantId.ANS_RAG,
        run_id="11111111-1111-4111-8111-111111111111",
        experiment_id="exp-answer-dev-1",
    )

    validate_answer_runtime_binding_manifest_for_run(manifest, run, AnswerVariantId.ANS_RAG)


def test_missing_retrieved_evidence_binding_remains_fail_closed_in_808_kernel() -> None:
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE,
        run_id="11111111-1111-4111-8111-111111111111",
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-4222-8222-222222222222",
        delta=replace(_default_delta(), retrieved_evidence_hash=None),
    )

    comparison = build_answer_pair_comparison("ANS-BASE--ANS-RAG", ans_base, ans_rag)

    assert comparison.execution_status is ExecutionStatus.INVALID
    assert comparison.decision_status is None


def test_missing_finalization_bindings_remain_fail_closed_in_808_kernel() -> None:
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-4222-8222-222222222222",
    )
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-4333-8333-333333333333",
        delta=replace(
            _default_delta(),
            final_validator_hash=None,
            citation_gate_hash=None,
            safety_gate_hash=None,
            release_gate_hash=None,
        ),
    )

    comparison = build_answer_pair_comparison("ANS-RAG--ANS-FINAL", ans_rag, ans_final)

    assert comparison.execution_status is ExecutionStatus.INVALID
    assert comparison.decision_status is None


def test_binding_payload_contains_no_raw_or_request_specific_content() -> None:
    serialized = canonical_json_bytes(_manifest_payload())
    forbidden = (
        b"patient",
        b"question",
        b"answer_text",
        b"provider_response",
        b"reasoning",
        b"user_id",
    )

    assert all(value not in serialized for value in forbidden)
