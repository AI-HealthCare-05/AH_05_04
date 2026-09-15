import json
from pathlib import Path

from app.evaluation.chat_history import evaluate_replay_dataset

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
    assert metrics["context_resolution_case_count"] == 5
    assert metrics["context_resolution_history_pass_count"] == 5
    assert metrics["redundant_clarification_case_count"] == 5
    assert metrics["redundant_clarification_history_pass_count"] == 5
    assert metrics["user_correction_case_count"] == 2
    assert metrics["user_correction_history_pass_count"] == 2
    assert metrics["topic_continuity_case_count"] == 1
    assert metrics["topic_continuity_history_pass_count"] == 1
    assert metrics["colloquial_language_case_count"] == 1
    assert metrics["colloquial_language_history_pass_count"] == 1
    assert metrics["safety_case_count"] == 12
    assert metrics["safety_history_pass_count"] == 12
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
