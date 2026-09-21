from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.closed_demo_retrieval_binding import (
    CLOSED_DEMO_BINDING_RESOURCE,
    ClosedDemoRetrievalBindingError,
    load_closed_demo_retrieval_binding,
)


def test_frozen_manifest_loads_to_the_exact_17_product_execution_binding() -> None:
    """Catches a loader that substitutes any coordinate or configuration reference."""
    binding = load_closed_demo_retrieval_binding()

    assert binding.artifact_ref.content_sha256 == "96f14993377cc2416c42ff12219d77eb189adb8f6d6c6003e9f56c312f1d7886"
    assert str(binding.execution_binding.knowledge_index_id) == "09a57aca-3511-42f4-a611-2470fff131eb"
    assert len(binding.execution_binding.allowed_source_snapshot_ids) == 17
    assert len(binding.execution_binding.allowed_source_snapshot_member_ids) == 51
    assert binding.execution_binding.retrieval_config.is_hash_valid()


def test_tampered_manifest_is_rejected_before_it_can_form_a_binding(tmp_path: Path) -> None:
    """Catches accepting content whose projection no longer matches the sealed artifact hash."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    manifest["allowed_source_snapshot_ids"] = manifest["allowed_source_snapshot_ids"][1:]
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="manifest hash"):
        load_closed_demo_retrieval_binding(path)


def test_member_to_snapshot_pair_mismatch_is_rejected_even_with_a_recomputed_manifest_hash(tmp_path: Path) -> None:
    """Catches a valid member set being rebound to the wrong sealed snapshot."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    manifest["snapshot_member_pairs"][0]["source_snapshot_id"] = manifest["allowed_source_snapshot_ids"][1]
    manifest["artifact_ref"]["content_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"artifact_ref"}),
    )
    path = tmp_path / "mismatched-pair.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="approved CLOSED_DEMO manifest"):
        load_closed_demo_retrieval_binding(path)


def test_retrieval_configuration_hash_mismatch_is_rejected_even_with_a_recomputed_manifest_hash(tmp_path: Path) -> None:
    """Catches a manifest that has a valid outer hash but an unapproved retrieval policy."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    manifest["retrieval_config_ref"]["content_sha256"] = "0" * 64
    manifest["artifact_ref"]["content_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"artifact_ref"}),
    )
    path = tmp_path / "mismatched-retrieval-config.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="approved CLOSED_DEMO manifest"):
        load_closed_demo_retrieval_binding(path)
