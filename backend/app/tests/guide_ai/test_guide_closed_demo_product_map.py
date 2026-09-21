from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from ai_worker.tasks.rag.guide_closed_demo_product_map import (
    load_guide_closed_demo_product_map,
)
from app.models.prescriptions import PrescriptionVersionMedication
from app.models.rag_candidate import MedicationIdentificationStatus
from app.services.guide_ai.closed_demo_generator import (
    GuideClosedDemoProductIdentityError,
    resolve_medication_item_seq,
)


def test_product_map_canonical_hash_and_exact_resolution() -> None:
    product_map = load_guide_closed_demo_product_map()
    assert product_map.product_count == 17
    assert product_map.artifact_ref.content_sha256 == "5f1e26adb671faac2f8143a34eb3a743d1b2ad8fc7bcaf63da9146eeea9acb3e"

    # Known exact mappings
    assert product_map.resolve("타이레놀정500밀리그람") == "202106092"
    assert product_map.resolve("노바스크정5밀리그램") == "200610660"
    assert product_map.resolve("다이크로짇정") == "196000008"

    # Unknown product returns None
    assert product_map.resolve("알수없는미등록약품") is None


def test_resolve_medication_item_seq_precedence_1st_identifications() -> None:
    """1st priority: MedicationIdentification (MATCHED, MFDS_ITEM_SEQ)."""
    product_map = load_guide_closed_demo_product_map()

    matched_ident = SimpleNamespace(
        status=MedicationIdentificationStatus.MATCHED,
        code_system="MFDS_ITEM_SEQ",
        canonical_code="200610660",
    )
    med = SimpleNamespace(
        medication_name="처방전에 적힌 임의 이름",
        strength_text=None,
        identifications=[matched_ident],
    )

    resolved = resolve_medication_item_seq(cast(PrescriptionVersionMedication, med), product_map)
    assert resolved == "200610660"


def test_resolve_medication_item_seq_precedence_2nd_product_map_fallback() -> None:
    """2nd priority: Fallback to sealed 17-product map when not matched in identifications."""
    product_map = load_guide_closed_demo_product_map()

    # Empty or unresolved identifications
    unresolved_ident = SimpleNamespace(
        status=MedicationIdentificationStatus.UNRESOLVED,
        code_system=None,
        canonical_code=None,
    )
    med = SimpleNamespace(
        medication_name="타이레놀정500밀리그람",
        strength_text=None,
        identifications=[unresolved_ident],
    )

    resolved = resolve_medication_item_seq(cast(PrescriptionVersionMedication, med), product_map)
    assert resolved == "202106092"


def test_resolve_medication_item_seq_precedence_3rd_fails_closed() -> None:
    """3rd priority: Fail closed if both 1st and 2nd do not match."""
    product_map = load_guide_closed_demo_product_map()

    med = SimpleNamespace(
        medication_name="완전히 알수없는 약물명",
        strength_text=None,
        identifications=[],
    )

    with pytest.raises(GuideClosedDemoProductIdentityError, match="Unable to resolve exact MFDS product identity"):
        resolve_medication_item_seq(cast(PrescriptionVersionMedication, med), product_map)
