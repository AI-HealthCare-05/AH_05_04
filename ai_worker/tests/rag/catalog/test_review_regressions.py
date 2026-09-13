import hashlib
import json
import unicodedata
from dataclasses import dataclass, replace

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateIndexBuildFailure,
    CandidateIndexBuildSuccess,
    build_candidate_index,
)
from ai_worker.tasks.rag.catalog import (
    CandidateCatalogSourceRef,
    CandidateEntryType,
    CatalogApprovalReceipt,
    CatalogBuildDecision,
    CatalogExportError,
    CatalogFreshnessStatus,
    CatalogSourceApproval,
    CatalogValidationFailureReason,
    CatalogVerificationStatus,
    build_catalog_candidate,
)
from ai_worker.tests.rag.catalog.test_build import (
    _alias_inputs,
    _component_inputs,
    _ingredient_inputs,
    _product_inputs,
)
from ai_worker.tests.rag.catalog.test_service import RecordingRepository, _request
from ai_worker.tests.rag.test_candidate_index import lexical_config


@dataclass
class SyntheticApprovalVerifier:
    source_status: CatalogVerificationStatus = CatalogVerificationStatus.APPROVED
    freshness: CatalogFreshnessStatus = CatalogFreshnessStatus.CURRENT
    approved: CatalogVerificationStatus = CatalogVerificationStatus.APPROVED
    complete: bool = True
    wrong_checksum: bool = False
    missing_source: bool = False
    receipt_id: str = "synthetic-catalog-approval-1"

    async def verify(self, *, catalog_version, export_checksum, source_refs):
        return CatalogApprovalReceipt(
            receipt_id=self.receipt_id,
            catalog_version=catalog_version,
            export_checksum="0" * 64 if self.wrong_checksum else export_checksum,
            verification_status=self.approved,
            is_complete=self.complete,
            sources=tuple(
                CatalogSourceApproval(ref, f"synthetic-source-approval-{i}", self.source_status, self.freshness)
                for i, ref in enumerate(source_refs)
            )
            if not self.missing_source
            else (),
        )


@pytest.mark.asyncio
async def test_complete_component_fixture_builds_from_independent_ingredient_registry() -> None:
    # The fixture deliberately also contains cross-product conflicting aliases; those
    # negative cases are tested separately, without dropping any product/component.
    aliases = tuple(
        alias for alias in _alias_inputs() if not alias.source_alias_ref.startswith("synthetic-alias-conflict")
    )
    request = replace(
        _request(),
        products=_product_inputs(),
        ingredients=_ingredient_inputs(),
        components=_component_inputs(),
        aliases=aliases,
        source_refs=(CandidateCatalogSourceRef("synthetic-snapshot-001", "synthetic-v1"),),
    )
    repository = RecordingRepository()
    result = await build_catalog_candidate(request=request, repository=repository)
    assert result.decision is CatalogBuildDecision.ACTIVATION_CANDIDATE
    assert result.export is not None
    assert len(result.export.catalog.components) == 3
    assert len(result.export.catalog.ingredients) == 2
    assert len(repository.saved) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["product", "ingredient", "alias"])
async def test_service_missing_reference_rejects_without_export_or_save(missing: str) -> None:
    request = replace(
        _request(),
        products=_product_inputs(),
        ingredients=_ingredient_inputs(),
        components=_component_inputs(),
        aliases=(),
    )
    if missing == "product":
        request = replace(request, products=())
    elif missing == "ingredient":
        request = replace(request, ingredients=())
    else:
        request = replace(request, aliases=(replace(_alias_inputs()[0], target_canonical_code="SYNTHETIC-SECRET"),))
    repository = RecordingRepository()
    result = await build_catalog_candidate(request=request, repository=repository)
    assert result.decision is CatalogBuildDecision.REJECTED
    assert result.export is None
    assert not repository.saved
    assert result.validation.failures[0].reason is CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID
    assert "SYNTHETIC-SECRET" not in str(result)


@pytest.mark.asyncio
async def test_cross_snapshot_repeated_alias_preserves_sources_and_deterministic_export() -> None:
    request = _request()
    alias = request.aliases[0]
    duplicate = replace(alias, source_snapshot_id="snapshot-alias-2", source_alias_ref="different-row")
    request = replace(
        request,
        aliases=(alias, duplicate),
        source_refs=(*request.source_refs, CandidateCatalogSourceRef("snapshot-alias-2", "v2")),
    )
    first = await build_catalog_candidate(request=request, repository=RecordingRepository())
    second = await build_catalog_candidate(
        request=replace(request, aliases=tuple(reversed(request.aliases))),
        repository=RecordingRepository(),
    )
    assert first.export is not None and second.export is not None
    assert first.export.manifest_json == second.export.manifest_json
    assert first.export.catalog_jsonl == second.export.catalog_jsonl
    catalog = first.export.catalog
    assert len(catalog.aliases) == 2
    entries = [e for e in catalog.search_entries if e.entry_type is CandidateEntryType.APPROVED_ALIAS]
    assert len(entries) == 1
    chosen = next(a for a in catalog.aliases if a.alias_ref == entries[0].alias_ref)
    assert chosen.source_snapshot_id == entries[0].source_snapshot_id
    assert {a.source_snapshot_id for a in catalog.aliases} == {"snapshot-001", "snapshot-alias-2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verifier",
    [
        None,
        SyntheticApprovalVerifier(source_status=CatalogVerificationStatus.NOT_APPROVED),
        SyntheticApprovalVerifier(freshness=CatalogFreshnessStatus.STALE),
        SyntheticApprovalVerifier(approved=CatalogVerificationStatus.NOT_APPROVED),
        SyntheticApprovalVerifier(complete=False),
    ],
)
async def test_missing_or_ineligible_approval_cannot_build_candidate_index(verifier) -> None:
    result = await build_catalog_candidate(
        request=_request(), repository=RecordingRepository(), approval_verifier=verifier
    )
    assert result.export is not None
    catalog = result.export.catalog
    assert catalog.verification_status is CatalogVerificationStatus.NOT_APPROVED
    index = build_candidate_index(
        result.export, replace(lexical_config(), normalization_version=catalog.normalization_version)
    )
    assert isinstance(index, CandidateIndexBuildFailure)


@pytest.mark.asyncio
async def test_approved_receipt_and_gate_fields_are_bound_in_manifest() -> None:
    result = await build_catalog_candidate(
        request=_request(),
        repository=RecordingRepository(),
        approval_verifier=SyntheticApprovalVerifier(),
    )
    assert result.export is not None
    catalog = result.export.catalog
    assert catalog.verification_status is CatalogVerificationStatus.APPROVED
    assert isinstance(
        build_candidate_index(
            result.export, replace(lexical_config(), normalization_version=catalog.normalization_version)
        ),
        CandidateIndexBuildSuccess,
    )
    payload = json.loads(result.export.manifest_json)
    expected_hash = payload.pop("catalog_manifest_hash")

    def digest(value):
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    assert digest(payload) == expected_hash
    for field, value in (
        ("verification_status", "NOT_APPROVED"),
        ("freshness_status", "STALE"),
        ("is_complete", False),
    ):
        assert digest({**payload, field: value}) != expected_hash
    other = await build_catalog_candidate(
        request=_request(),
        repository=RecordingRepository(),
        approval_verifier=SyntheticApprovalVerifier(receipt_id="synthetic-other-approval"),
    )
    assert other.export is not None
    assert other.export.catalog.catalog_manifest_hash != expected_hash
    assert other.export.export_checksum == result.export.export_checksum


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verifier", [SyntheticApprovalVerifier(wrong_checksum=True), SyntheticApprovalVerifier(missing_source=True)]
)
async def test_receipt_for_different_content_or_source_never_reaches_repository(verifier) -> None:
    repository = RecordingRepository()
    with pytest.raises(CatalogExportError, match="CATALOG_APPROVAL_BINDING_INVALID"):
        await build_catalog_candidate(request=_request(), repository=repository, approval_verifier=verifier)
    assert not repository.saved


@pytest.mark.asyncio
async def test_gate_or_export_tampering_is_rejected_at_handoff() -> None:
    from ai_worker.tasks.rag.catalog import verify_catalog_export

    result = await build_catalog_candidate(request=_request(), repository=RecordingRepository())
    assert result.export is not None
    verify_catalog_export(result.export)
    altered = replace(
        result.export,
        catalog=replace(
            result.export.catalog,
            verification_status=CatalogVerificationStatus.APPROVED,
            freshness_status=CatalogFreshnessStatus.CURRENT,
        ),
    )
    with pytest.raises(CatalogExportError, match="CATALOG_MANIFEST_BINDING_INVALID"):
        verify_catalog_export(altered)
    with pytest.raises(CatalogExportError, match="CATALOG_MANIFEST_BINDING_INVALID"):
        verify_catalog_export(replace(result.export, catalog_jsonl=result.export.catalog_jsonl + b"\n"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["raw", "gate", "freshness", "complete", "member", "manifest", "jsonl", "checksum"]
)
async def test_public_candidate_handoff_rejects_unbound_inputs_before_embedding(mutation) -> None:
    from typing import Any, cast

    from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildFailureReason
    from ai_worker.tests.rag.test_candidate_index import hybrid_config

    result = await build_catalog_candidate(
        request=_request(), repository=RecordingRepository(), approval_verifier=SyntheticApprovalVerifier()
    )
    artifacts = result.export
    assert artifacts is not None
    catalog = artifacts.catalog
    invalid: object
    if mutation == "raw":
        invalid = catalog
    elif mutation == "gate":
        invalid = replace(
            artifacts, catalog=replace(catalog, verification_status=CatalogVerificationStatus.NOT_APPROVED)
        )
    elif mutation == "freshness":
        invalid = replace(artifacts, catalog=replace(catalog, freshness_status=CatalogFreshnessStatus.STALE))
    elif mutation == "complete":
        invalid = replace(artifacts, catalog=replace(catalog, is_complete=False))
    elif mutation == "member":
        invalid = replace(
            artifacts,
            catalog=replace(
                catalog, products=(replace(catalog.products[0], product_name="tampered"), *catalog.products[1:])
            ),
        )
    elif mutation == "manifest":
        payload = json.loads(artifacts.manifest_json)
        payload["approval_receipt"] = None
        invalid = replace(artifacts, manifest_json=json.dumps(payload).encode())
    elif mutation == "jsonl":
        invalid = replace(artifacts, catalog_jsonl=artifacts.catalog_jsonl + b"{}\n")
    else:
        invalid = replace(artifacts, export_checksum="0" * 64)

    class NeverEmbed:
        def __getattr__(self, name):
            pytest.fail("unverified handoff reached embedding port")

    outcome = build_candidate_index(
        cast(Any, invalid),
        replace(hybrid_config(), normalization_version=catalog.normalization_version),
        cast(Any, NeverEmbed()),
    )
    assert isinstance(outcome, CandidateIndexBuildFailure)
    assert outcome.reason is CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("code_system", ["HIRA", "HIRA_EDI", "EDI", "NHIS", "NHIS_CODE", "UNKNOWN", "MFDS_UNKNOWN"])
async def test_non_allowlisted_identities_are_excluded_before_target_lookup(code_system) -> None:
    from ai_worker.tasks.rag.catalog.types import CandidateEntityType

    request = _request()
    foreign_product = replace(request.products[0], code_system=code_system, canonical_code="missing-insurance")
    foreign_ingredient = replace(_ingredient_inputs()[0], code_system=code_system)
    foreign_alias = replace(
        request.aliases[0], target_code_system=code_system, target_canonical_code="missing-insurance"
    )
    foreign_component = replace(
        _component_inputs()[0], product_code_system=code_system, product_canonical_code="missing-insurance"
    )
    result = await build_catalog_candidate(
        request=replace(
            request,
            products=(*request.products, foreign_product),
            ingredients=(*request.ingredients, foreign_ingredient),
            components=(*request.components, foreign_component),
            aliases=(*request.aliases, foreign_alias),
        ),
        repository=RecordingRepository(),
        approval_verifier=SyntheticApprovalVerifier(),
    )
    assert result.export is not None
    catalog = result.export.catalog
    assert all(p.identity.code_system == "MFDS_ITEM_SEQ" for p in catalog.products)
    assert all(i.identity.code_system == "MFDS_INGREDIENT_CODE" for i in catalog.ingredients)
    assert all(
        a.identity.code_system
        == ("MFDS_ITEM_SEQ" if a.identity.entity_type is CandidateEntityType.PRODUCT else "MFDS_INGREDIENT_CODE")
        for a in catalog.aliases
    )
    assert isinstance(
        build_candidate_index(
            result.export, replace(lexical_config(), normalization_version=catalog.normalization_version)
        ),
        CandidateIndexBuildSuccess,
    )


@pytest.mark.asyncio
async def test_unapproved_typed_catalog_cannot_be_promoted_around_manifest_gate() -> None:
    from typing import Any, cast

    from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildFailureReason

    result = await build_catalog_candidate(request=_request(), repository=RecordingRepository())
    assert result.export is not None
    promoted = replace(
        result.export.catalog,
        verification_status=CatalogVerificationStatus.APPROVED,
        freshness_status=CatalogFreshnessStatus.CURRENT,
        is_complete=True,
    )
    for invalid in (promoted, replace(result.export, catalog=promoted)):
        outcome = build_candidate_index(
            cast(Any, invalid), replace(lexical_config(), normalization_version=promoted.normalization_version)
        )
        assert isinstance(outcome, CandidateIndexBuildFailure)
        assert outcome.reason is CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID


@pytest.mark.asyncio
async def test_nfd_source_display_survives_approved_public_candidate_handoff() -> None:
    request = _request()
    product = request.products[0]
    alias = request.aliases[0]
    raw_product = unicodedata.normalize("NFD", product.product_name)
    raw_alias = unicodedata.normalize("NFD", alias.alias_text)
    ingredient = replace(_ingredient_inputs()[0], source_snapshot_id="snapshot-001")
    raw_ingredient = unicodedata.normalize("NFD", ingredient.ingredient_name)
    request = replace(
        request,
        products=(
            replace(
                product,
                product_name=raw_product,
                strength_text=unicodedata.normalize("NFD", "합성 함량"),
                dosage_form=unicodedata.normalize("NFD", "정제"),
                manufacturer_name=unicodedata.normalize("NFD", "합성제약"),
            ),
        ),
        aliases=(replace(alias, alias_text=raw_alias),),
        ingredients=(replace(ingredient, ingredient_name=raw_ingredient),),
    )
    result = await build_catalog_candidate(
        request=request, repository=RecordingRepository(), approval_verifier=SyntheticApprovalVerifier()
    )
    assert result.export is not None
    catalog = result.export.catalog
    assert catalog.products[0].product_name == raw_product
    assert catalog.ingredients[0].ingredient_name == raw_ingredient
    assert catalog.aliases[0].alias_text == raw_alias
    for raw, normalized in (
        (raw_product, catalog.products[0].normalized_product_name),
        (raw_ingredient, catalog.ingredients[0].normalized_ingredient_name),
        (raw_alias, catalog.aliases[0].normalized_alias),
    ):
        assert not unicodedata.is_normalized("NFC", raw)
        assert normalized == " ".join(unicodedata.normalize("NFC", raw).split())
    index = build_candidate_index(
        result.export, replace(lexical_config(), normalization_version=catalog.normalization_version)
    )
    assert isinstance(index, CandidateIndexBuildSuccess)
    assert {member.display_text for member in index.members} == {raw_product, raw_alias}
    assert all(unicodedata.is_normalized("NFC", member.normalized_text) for member in index.members)

    # Byte-distinct source displays must not silently collapse to the same member hash.
    nfc_result = await build_catalog_candidate(
        request=replace(
            request, products=(replace(request.products[0], product_name=product.product_name),), aliases=(alias,)
        ),
        repository=RecordingRepository(),
        approval_verifier=SyntheticApprovalVerifier(),
    )
    assert nfc_result.export is not None
    nfc_index = build_candidate_index(
        nfc_result.export, replace(lexical_config(), normalization_version=catalog.normalization_version)
    )
    assert isinstance(nfc_index, CandidateIndexBuildSuccess)
    assert {member.member_content_hash for member in index.members} != {
        member.member_content_hash for member in nfc_index.members
    }
