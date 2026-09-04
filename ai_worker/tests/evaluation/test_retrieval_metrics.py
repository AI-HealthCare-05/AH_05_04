from decimal import Decimal

from ai_worker.tasks.evaluation.retrieval_metrics import (
    RetrievalObservation,
    aggregate_metric_scores,
    metric_scores,
)


def test_metric_scores_match_hand_calculated_top_five_retrieval() -> None:
    observation = RetrievalObservation(
        required_ids=("required",),
        relevant_ids=("required", "secondary"),
        ranked_ids=("noise", "required", "secondary", "other", "last"),
    )

    assert metric_scores(observation) == {
        "RECALL_AT_5": Decimal("1.000000"),
        "PRECISION_AT_5": Decimal("0.400000"),
        "MRR": Decimal("0.500000"),
        "NDCG_AT_5": Decimal("0.693426"),
        "NO_HIT_RATE": Decimal("0.000000"),
    }


def test_metric_scores_reject_zero_required_denominator() -> None:
    observation = RetrievalObservation(
        required_ids=(),
        relevant_ids=("relevant",),
        ranked_ids=("relevant",),
    )

    assert metric_scores(observation) is None


def test_aggregate_metric_scores_marks_insufficient_sample_inconclusive() -> None:
    scores = [
        metric_scores(
            RetrievalObservation(("required",), ("required",), ("required",)),
        )
        for _ in range(4)
    ]

    aggregate = aggregate_metric_scores(scores, minimum_case_count=5)

    assert aggregate["RECALL_AT_5"].value == Decimal("1.000000")
    assert aggregate["RECALL_AT_5"].numerator == 4
    assert aggregate["RECALL_AT_5"].denominator == 4
    assert aggregate["RECALL_AT_5"].reason_code == "MINIMUM_CASE_COUNT_NOT_MET"
