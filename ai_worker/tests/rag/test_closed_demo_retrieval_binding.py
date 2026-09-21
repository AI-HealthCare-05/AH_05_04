from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.closed_demo_retrieval_binding import (
    CLOSED_DEMO_BINDING_RESOURCE,
    ClosedDemoProductScope,
    ClosedDemoRetrievalBindingError,
    load_closed_demo_retrieval_binding,
    scope_for_item_seq,
)

_APPROVED_SHA256 = "ce14b2b314dd3ddd8e32e816559792711b3200fe1eb64b7c2d0c84916d31a0c4"


def test_frozen_manifest_loads_to_the_exact_17_product_execution_binding() -> None:
    """Catches a loader that substitutes any coordinate or configuration reference."""
    binding = load_closed_demo_retrieval_binding()

    assert binding.artifact_ref.content_sha256 == _APPROVED_SHA256
    assert binding.artifact_ref.version == "1.1.0"
    assert str(binding.execution_binding.knowledge_index_id) == "09a57aca-3511-42f4-a611-2470fff131eb"
    assert len(binding.execution_binding.allowed_source_snapshot_ids) == 17
    assert len(binding.execution_binding.allowed_source_snapshot_member_ids) == 51
    assert binding.execution_binding.retrieval_config.is_hash_valid()


def test_product_scopes_cover_exact_17_products_and_51_members() -> None:
    """Verifies that product_scopes exactly cover the 17 products, 51 members, and pairs."""
    binding = load_closed_demo_retrieval_binding()

    assert len(binding.product_scopes) == 17
    seen_snapshots = set()
    seen_members = set()
    allowed_pairs = {
        (p.source_snapshot_id, p.source_snapshot_member_id)
        for p in binding.snapshot_member_pairs
    }

    for scope in binding.product_scopes:
        assert isinstance(scope, ClosedDemoProductScope)
        assert len(scope.item_seq) == 9 and scope.item_seq.isdigit()
        assert len(scope.source_snapshot_member_ids) == 3
        assert len(set(scope.source_snapshot_member_ids)) == 3

        seen_snapshots.add(scope.source_snapshot_id)
        for m_id in scope.source_snapshot_member_ids:
            seen_members.add(m_id)
            assert (scope.source_snapshot_id, m_id) in allowed_pairs

    assert seen_snapshots == set(binding.execution_binding.allowed_source_snapshot_ids)
    assert seen_members == set(binding.execution_binding.allowed_source_snapshot_member_ids)
    assert len(seen_members) == 51


def test_scope_for_item_seq_resolution_and_fail_closed() -> None:
    """Verifies scope resolution works for valid item_seq and fails closed otherwise."""
    binding = load_closed_demo_retrieval_binding()

    # Valid smoke products
    diabex = scope_for_item_seq("198500321")
    assert diabex.item_seq == "198500321"
    assert binding.scope_for_item_seq("198500321") == diabex

    januvia = scope_for_item_seq("200710759")
    assert januvia.item_seq == "200710759"

    norvasc = scope_for_item_seq("200610660")
    assert norvasc.item_seq == "200610660"

    # Invalid item_seq formats fail closed
    with pytest.raises(ClosedDemoRetrievalBindingError, match="invalid"):
        scope_for_item_seq("not-an-item-seq")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="invalid"):
        scope_for_item_seq("12345")

    # Unknown 9-digit item_seq fails closed
    with pytest.raises(ClosedDemoRetrievalBindingError, match="no product scope"):
        scope_for_item_seq("999999999")


def test_tampered_manifest_is_rejected_before_it_can_form_a_binding(tmp_path: Path) -> None:
    """Catches accepting content whose projection no longer matches the sealed artifact hash."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    manifest["allowed_source_snapshot_ids"] = manifest["allowed_source_snapshot_ids"][1:]
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="manifest hash"):
        load_closed_demo_retrieval_binding(path)


def test_tampered_product_scope_missing_product_fails_closed(tmp_path: Path) -> None:
    """Catches a product scope set that only has 16 products instead of 17."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    first_key = next(iter(manifest["product_scopes"]))
    del manifest["product_scopes"][first_key]
    manifest["artifact_ref"]["content_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"artifact_ref"}),
    )
    path = tmp_path / "missing-product.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="approved CLOSED_DEMO manifest"):
        load_closed_demo_retrieval_binding(path)


def test_tampered_product_scope_invalid_member_count_fails_closed(tmp_path: Path) -> None:
    """Catches a product scope with 2 members instead of 3."""
    manifest = json.loads(CLOSED_DEMO_BINDING_RESOURCE.read_text(encoding="utf-8"))
    first_key = next(iter(manifest["product_scopes"]))
    manifest["product_scopes"][first_key]["source_snapshot_member_ids"] = (
        manifest["product_scopes"][first_key]["source_snapshot_member_ids"][:2]
    )
    manifest["artifact_ref"]["content_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"artifact_ref"}),
    )
    path = tmp_path / "invalid-member-count.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClosedDemoRetrievalBindingError, match="approved CLOSED_DEMO manifest"):
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
