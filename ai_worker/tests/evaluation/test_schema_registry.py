from __future__ import annotations

from ai_worker.tasks.evaluation.schema_registry import (
    _SCHEMA_SET_MEMBER_COUNTS,
    SCHEMA_REGISTRIES,
    SCHEMA_REGISTRY_V1_4,
    SCHEMA_REGISTRY_V1_5,
)


def test_schema_registry_v1_5_registered_and_member_count_is_26() -> None:
    assert "1.5.0" in SCHEMA_REGISTRIES
    registry_v1_5 = SCHEMA_REGISTRIES["1.5.0"]
    assert registry_v1_5 is SCHEMA_REGISTRY_V1_5
    assert len(registry_v1_5) == 26
    assert _SCHEMA_SET_MEMBER_COUNTS["1.5.0"] == 26


def test_schema_registry_v1_5_paths_and_ids_are_unique() -> None:
    registry_v1_5 = SCHEMA_REGISTRIES["1.5.0"]
    paths = [entry.relative_path for entry in registry_v1_5]
    schema_ids = [entry.schema_id for entry in registry_v1_5]

    assert len(paths) == len(set(paths)) == 26
    assert len(schema_ids) == len(set(schema_ids)) == 26


def test_schema_registry_v1_4_unchanged_and_strictly_preserved() -> None:
    assert len(SCHEMA_REGISTRY_V1_4) == 23
    assert _SCHEMA_SET_MEMBER_COUNTS["1.4.0"] == 23

    registry_v1_4 = SCHEMA_REGISTRIES["1.4.0"]
    registry_v1_5 = SCHEMA_REGISTRIES["1.5.0"]

    # First 23 entries of 1.5.0 must match 1.4.0 exactly
    assert registry_v1_5[:23] == registry_v1_4


def test_schema_registry_v1_5_adds_exact_three_answer_quality_members() -> None:
    registry_v1_4 = SCHEMA_REGISTRIES["1.4.0"]
    registry_v1_5 = SCHEMA_REGISTRIES["1.5.0"]

    v1_4_ids = {entry.schema_id for entry in registry_v1_4}
    v1_5_ids = {entry.schema_id for entry in registry_v1_5}

    added_ids = v1_5_ids - v1_4_ids
    assert added_ids == {
        "rag-eval.answer-human-judgment",
        "rag-eval.answer-human-judgment-approval",
        "rag-eval.answer-comparison-set-manifest",
    }
