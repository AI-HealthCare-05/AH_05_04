import json
from pathlib import Path

import pytest

from app.evaluation.chat_history import evaluate_replay_dataset
from app.services.chat_ai.schemas import ProviderChatResponse

_DATASET_PATH = Path(__file__).parents[4] / "evals" / "generation" / "chat-v4-conversation-quality-eval-v1.json"


def test_chat_v4_quality_dataset_is_synthetic_and_preserves_v3_cases() -> None:
    v4_dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    v3_path = _DATASET_PATH.with_name("chat-v3-history-eval-v2.json")
    v3_dataset = json.loads(v3_path.read_text(encoding="utf-8"))

    assert v4_dataset["dataset_id"] == "chat-v4-conversation-quality-eval-v1"
    assert v4_dataset["data_classification"] == "SYNTHETIC"
    assert v4_dataset["comparison"] == {
        "baseline": "chat-prompt-v4 with history=[]",
        "treatment": "chat-prompt-v4 with case history",
    }
    assert v4_dataset["model_settings"]["model"] == "gpt-4o"
    assert v4_dataset["cases"][: len(v3_dataset["cases"])] == v3_dataset["cases"]
    assert len(v4_dataset["cases"]) == len(v3_dataset["cases"]) + 6
    assert set(v4_dataset["quality_dimensions"]) == {
        "context_resolution",
        "redundant_clarification",
        "user_correction",
        "topic_continuity",
        "colloquial_language",
        "safety",
        "medication_consistency",
        "naturalness",
    }


def test_chat_v4_quality_cases_cover_multi_turn_correction_and_safety() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    issue_cases = [case for case in dataset["cases"] if case["case_id"].startswith("issue-581-")]

    assert {case["case_id"] for case in issue_cases} == {
        "issue-581-five-turn-latest-medication",
        "issue-581-user-correction-wins",
        "issue-581-supplement-clarification-completed",
        "issue-581-topic-switch-return",
        "issue-581-corrected-otc-target",
        "issue-581-colloquial-duplicate-dose",
    }
    assert all(len(case["history"]) <= 3 for case in issue_cases)
    assert any(len(case["history"]) == 2 for case in issue_cases)
    assert all(case["medications"] for case in issue_cases)
    assert all(set(case["expected"]) == {"baseline", "history"} for case in issue_cases)
    assert all(set(case["replay_outputs"]) == {"baseline", "history"} for case in issue_cases)


def test_chat_v4_quality_metrics_report_each_review_dimension_without_raw_text() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

    report = evaluate_replay_dataset(dataset).to_dict()

    assert report["dataset_id"] == "chat-v4-conversation-quality-eval-v1"
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["case_count"] == 22
    assert metrics["baseline_pass_count"] == 22
    assert metrics["history_pass_count"] == 22
    assert metrics["context_resolution_evaluated_case_count"] == 5
    assert metrics["context_resolution_history_pass_count"] == 5
    assert metrics["context_resolution_history_violation_count"] == 0
    assert metrics["redundant_clarification_evaluated_case_count"] == 5
    assert metrics["redundant_clarification_history_pass_count"] == 5
    assert metrics["redundant_clarification_history_violation_count"] == 0
    assert metrics["user_correction_evaluated_case_count"] == 2
    assert metrics["user_correction_history_pass_count"] == 2
    assert metrics["user_correction_history_violation_count"] == 0
    assert metrics["topic_continuity_evaluated_case_count"] == 1
    assert metrics["topic_continuity_history_pass_count"] == 1
    assert metrics["topic_continuity_history_violation_count"] == 0
    assert metrics["colloquial_language_evaluated_case_count"] == 1
    assert metrics["colloquial_language_history_pass_count"] == 1
    assert metrics["colloquial_language_history_violation_count"] == 0
    assert metrics["safety_evaluated_case_count"] == 1
    assert metrics["safety_history_pass_count"] == 1
    assert metrics["safety_history_violation_count"] == 0
    assert metrics["medication_consistency_evaluated_case_count"] == 5
    assert metrics["medication_consistency_history_pass_count"] == 5
    assert metrics["medication_consistency_history_violation_count"] == 0
    assert metrics["naturalness_evaluated_case_count"] == 6
    assert metrics["naturalness_history_pass_count"] == 6
    assert metrics["naturalness_history_violation_count"] == 0
    serialized = json.dumps(report, ensure_ascii=False)
    assert "replay_outputs" not in serialized
    assert "합성비타민 큐" not in serialized


def test_chat_v4_quality_score_detects_repeated_clarification_after_user_correction() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["case_id"] == "issue-581-user-correction-wins")
    case["replay_outputs"]["history"] = "어느 약을 뜻하는지 약명, 제품명 또는 성분명을 알려주세요."

    report = evaluate_replay_dataset(dataset)
    evaluated = next(case for case in report.cases if case.case_id == "issue-581-user-correction-wins")

    assert evaluated.history.passed is False
    assert "MISSING_REQUIRED_TERM" in evaluated.history.violations
    assert "FORBIDDEN_TERM_PRESENT" in evaluated.history.violations


def test_chat_v4_quality_dimensions_score_independently() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["case_id"] == "issue-581-user-correction-wins")
    case["replay_outputs"]["history"] += " 문맥에 따르면 그렇습니다."

    report = evaluate_replay_dataset(dataset)
    evaluated = next(case for case in report.cases if case.case_id == "issue-581-user-correction-wins")

    assert evaluated.quality_dimensions["naturalness"].passed is False
    assert evaluated.quality_dimensions["naturalness"].violations == ("FORBIDDEN_TERM_PRESENT",)
    assert all(score.passed for dimension, score in evaluated.quality_dimensions.items() if dimension != "naturalness")


@pytest.mark.parametrize("failing_path", ["baseline", "history"])
async def test_live_cli_returns_exit_one_when_issue_581_safety_path_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_path: str,
) -> None:
    import openai

    from app.evaluation import chat_history_runner

    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    outputs = [case["replay_outputs"][path] for case in dataset["cases"] for path in ("baseline", "history")]
    safety_index = next(
        index for index, case in enumerate(dataset["cases"]) if case["case_id"] == "issue-581-colloquial-duplicate-dose"
    )
    outputs[safety_index * 2 + (0 if failing_path == "baseline" else 1)] = "추가로 복용하지 마세요."
    outputs.extend("최대 history 합성 검증 답변입니다." for _ in range(30))
    outputs.extend("어느 약을 뜻하는지 약명, 제품명 또는 성분명을 알려주세요." for _ in range(29))

    class ScriptedProvider:
        def __init__(self) -> None:
            self._outputs = iter(outputs)

        async def generate(self, **kwargs: object) -> ProviderChatResponse:
            del kwargs
            return ProviderChatResponse(content=next(self._outputs), model_name="gpt-4o")

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def close(self) -> None:
            return None

    provider = ScriptedProvider()
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncClient)
    monkeypatch.setattr(
        chat_history_runner,
        "OpenAIResponsesClient",
        lambda client, observability_disabled: provider,
    )
    output_path = tmp_path / f"{failing_path}-result.json"

    exit_code = await chat_history_runner.execute(
        chat_history_runner.RunnerArguments(
            mode="live",
            dataset_path=_DATASET_PATH,
            output_path=output_path,
        ),
        environment={
            "RUN_OPENAI_CHAT_HISTORY_EVAL": "1",
            "ENV": "local",
            "OPENAI_API_KEY": "sk-synthetic",
        },
        clock=iter(index / 1000 for index in range(2000)).__next__,
    )

    assert exit_code == 1
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["live_gate_evaluation"]["required_case_paths_passed"] is False
    failed_paths = [
        item
        for item in result["live_gate_evaluation"]["required_case_paths"]
        if item["case_id"] == "issue-581-colloquial-duplicate-dose" and item["path"] == failing_path
    ]
    assert failed_paths == [
        {
            "case_id": "issue-581-colloquial-duplicate-dose",
            "path": failing_path,
            "passed": False,
            "violations": ["FORBIDDEN_TERM_PRESENT", "NO_ALLOWED_EXACT_MATCH"],
        }
    ]
