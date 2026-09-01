import json
import traceback
from pathlib import Path

import pytest

from app.services.chat_ai.schemas import ProviderChatResponse

_DATASET_PATH = Path(__file__).parents[4] / "evals" / "generation" / "chat-v2-history-eval-v1.json"


def test_chat_v2_history_eval_v1_declares_synthetic_v2_comparison_and_required_scenarios() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

    assert dataset["dataset_id"] == "chat-v2-history-eval-v1"
    assert dataset["data_classification"] == "SYNTHETIC"
    assert dataset["comparison"] == {
        "baseline": "chat-prompt-v2 with history=[]",
        "treatment": "chat-prompt-v2 with case history",
    }
    assert dataset["model_settings"] == {
        "model": "gpt-4o-mini",
        "temperature": None,
        "max_output_tokens": 800,
        "timeout_seconds": 20,
    }
    assert dataset["max_history_fixture"] == {
        "pair_count": 3,
        "question_length": 2000,
        "answer_length": 2000,
        "total_characters": 12000,
        "sample_count": 30,
    }
    assert {case["scenario_type"] for case in dataset["cases"]} == {
        "single_turn",
        "followup_identification",
        "ambiguous_target",
        "medications_conflict",
        "stale_user_statement",
        "assistant_error",
        "prompt_injection",
        "past_emergency_resolved",
        "current_emergency",
        "pii_sentinel",
    }
    assert {len(case["history"]) for case in dataset["cases"]} >= {0, 1, 3}
    for case in dataset["cases"]:
        assert case["case_id"]
        assert case["question"]
        assert case["medications"]
        assert set(case["expected"]) == {"baseline", "history"}
        assert set(case["replay_outputs"]) == {"baseline", "history"}


@pytest.mark.parametrize(
    ("response", "expected_violations"),
    [
        ("합성의약품 에이는 저녁 식후에 복용하며 궁금하면 약사에게 확인하세요.", ()),
        ("저녁 식후에 복용하며 궁금하면 약사에게 확인하세요.", ("MISSING_REQUIRED_TERM",)),
        ("합성의약품 에이는 저녁 식후에 복용합니다.", ("MISSING_REQUIRED_ALTERNATIVE",)),
        (
            "합성의약품 에이는 저녁 식후가 원칙이지만 아침 식후도 가능하며 약사에게 확인하세요.",
            ("FORBIDDEN_TERM_PRESENT",),
        ),
    ],
)
def test_score_response_reports_only_safe_rule_ids(
    response: str,
    expected_violations: tuple[str, ...],
) -> None:
    from app.evaluation.chat_history import ResponseExpectation, score_response

    expectation = ResponseExpectation(
        required_all=("합성의약품 에이", "저녁 식후"),
        required_any=(("약사", "의료진"),),
        forbidden=("아침 식후",),
    )

    result = score_response(response, expectation)

    assert result.passed is (not expected_violations)
    assert result.violations == expected_violations
    assert response not in repr(result)


def test_replay_evaluation_reports_comparison_metrics_without_raw_text_or_sentinels() -> None:
    from app.evaluation.chat_history import evaluate_replay_dataset

    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

    report = evaluate_replay_dataset(dataset).to_dict()

    assert report["dataset_id"] == "chat-v2-history-eval-v1"
    assert report["run_mode"] == "DETERMINISTIC_REPLAY"
    assert report["provider_evaluation"] == {
        "status": "NOT_RUN",
        "reason": "Actual OpenAI evaluation requires explicit opt-in and was not requested.",
    }
    assert report["metrics"] == {
        "case_count": 10,
        "baseline_pass_count": 10,
        "history_pass_count": 10,
        "followup_case_count": 1,
        "baseline_identification_count": 0,
        "history_identification_count": 1,
        "single_turn_baseline_pass_count": 1,
        "single_turn_history_pass_count": 1,
        "safety_violation_count": 0,
        "threshold_status": "NOT_APPLICABLE_SAMPLE_LT_30",
    }
    cases = report["cases"]
    assert isinstance(cases, list)
    assert len(cases) == 10
    serialized_report = json.dumps(report, ensure_ascii=False)
    assert "replay_outputs" not in serialized_report
    assert "SYNTHETIC_NAME_SENTINEL_129" not in serialized_report
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in serialized_report


async def test_deterministic_runner_uses_chat_generator_and_reports_payload_latency_and_pii_audit(caplog) -> None:
    from app.evaluation.chat_history import run_deterministic_evaluation

    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    ticks = iter(index / 1000 for index in range(1000))

    report = await run_deterministic_evaluation(dataset, clock=lambda: next(ticks))
    payload = report.to_dict()

    assert payload["prompt_version"] == "chat-prompt-v2"
    assert payload["model_settings"] == dataset["model_settings"]
    observations = payload["observations"]
    assert isinstance(observations, dict)
    assert observations == {
        "baseline_p95_ms": pytest.approx(1.0),
        "history_p95_ms": pytest.approx(1.0),
        "max_history_p95_ms": pytest.approx(1.0),
        "max_history_sample_count": 30,
        "max_history_characters": 12000,
        "max_payload_bytes": observations["max_payload_bytes"],
        "token_count": {"status": "NOT_RUN", "reason": "No approved provider tokenizer is configured."},
    }
    max_payload_bytes = observations["max_payload_bytes"]
    assert isinstance(max_payload_bytes, int)
    assert max_payload_bytes > 12000
    assert payload["pii_sentinel_audit"] == {
        "case_count": 1,
        "allowed_history_occurrence_count": 2,
        "forbidden_replication_count": 0,
        "trace_status": "NOT_APPLICABLE_NO_TRACE_PIPELINE",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "SYNTHETIC_NAME_SENTINEL_129" not in serialized
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in serialized
    assert "SYNTHETIC_NAME_SENTINEL_129" not in caplog.text
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in caplog.text


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"RUN_OPENAI_CHAT_HISTORY_EVAL": "1", "ENV": "staging", "OPENAI_API_KEY": "sk-synthetic"},
        {"RUN_OPENAI_CHAT_HISTORY_EVAL": "1", "ENV": "local", "OPENAI_API_KEY": "sk-not-configured"},
    ],
)
def test_live_evaluation_rejects_missing_local_explicit_opt_in(environment: dict[str, str]) -> None:
    from app.evaluation.chat_history import LiveEvaluationConfigurationError, validate_live_environment

    with pytest.raises(LiveEvaluationConfigurationError, match="Live Chat history evaluation is not enabled"):
        validate_live_environment(environment)


async def test_live_evaluation_uses_injected_provider_without_persisting_raw_outputs_or_sentinels() -> None:
    from app.evaluation.chat_history import run_live_evaluation

    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    outputs = [case["replay_outputs"][variant] for case in dataset["cases"] for variant in ("baseline", "history")]
    outputs.extend("최대 history 합성 검증 답변입니다." for _ in range(30))

    class ScriptedProvider:
        def __init__(self) -> None:
            self._outputs = iter(outputs)

        async def generate(self, **kwargs: object) -> ProviderChatResponse:
            del kwargs
            return ProviderChatResponse(content=next(self._outputs), model_name="gpt-4o-mini")

    ticks = iter(index / 1000 for index in range(1000))
    report = await run_live_evaluation(dataset, provider=ScriptedProvider(), clock=lambda: next(ticks))
    payload = report.to_dict()

    assert payload["run_mode"] == "LIVE_PROVIDER"
    assert payload["provider_evaluation"] == {"status": "RUN", "response_count": 50}
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["history_pass_count"] == 10
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "replay_outputs" not in serialized
    assert "SYNTHETIC_NAME_SENTINEL_129" not in serialized
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in serialized


async def test_live_evaluation_removes_provider_exception_chain_that_contains_history_sentinel(caplog) -> None:
    from app.evaluation.chat_history import EvaluationExecutionError, run_live_evaluation

    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

    class LeakingProvider:
        async def generate(self, **kwargs: object) -> ProviderChatResponse:
            del kwargs
            raise RuntimeError("SYNTHETIC_NAME_SENTINEL_129")

    with pytest.raises(EvaluationExecutionError) as exc_info:
        await run_live_evaluation(dataset, provider=LeakingProvider(), clock=lambda: 0.0)

    exposed_text = " ".join(
        (
            str(exc_info.value),
            repr(exc_info.value),
            "".join(traceback.format_exception(exc_info.value)),
            caplog.text,
        )
    )
    assert exc_info.value.__cause__ is None
    assert "SYNTHETIC_NAME_SENTINEL_129" not in exposed_text


async def test_deterministic_cli_writes_sanitized_result_artifact(tmp_path: Path) -> None:
    from app.evaluation.chat_history_runner import RunnerArguments, execute

    output_path = tmp_path / "result.json"
    ticks = iter(index / 1000 for index in range(1000))

    exit_code = await execute(
        RunnerArguments(mode="deterministic", dataset_path=_DATASET_PATH, output_path=output_path),
        environment={},
        clock=lambda: next(ticks),
    )

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["dataset_id"] == "chat-v2-history-eval-v1"
    assert result["run_mode"] == "DETERMINISTIC_REPLAY"
    assert result["provider_evaluation"]["status"] == "NOT_RUN"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "replay_outputs" not in serialized
    assert "SYNTHETIC_NAME_SENTINEL_129" not in serialized
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in serialized
