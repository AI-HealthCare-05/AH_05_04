"""Synthetic allowlist, scope, parser/version and safe location contract tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_IDENTITY,
    PRODUCT_REJECT_PARSER_VERSION,
    REJECT_CODE_CONTRACT_VERSION,
    ProductRejectCode,
    RejectContractError,
    validate_parser_contract,
    validate_reject_artifact,
)


def validate(**changes):
    args = dict(
        identity=PRODUCT_REJECT_IDENTITY,
        version=REJECT_CODE_CONTRACT_VERSION,
        code="ITEM_SEQ_REQUIRED",
        location="page[1].record[0]",
    )
    args.update(changes)
    validate_reject_artifact(**args)


@pytest.mark.parametrize("code", list(ProductRejectCode))
def test_all_codes_have_one_documented_contract(code):
    validate(code=code)
    root = Path(__file__).resolve().parents[4]
    contract = (root / "docs/contracts/proposed/post-mvp-1/source-reject-codes-v1.md").read_text()
    assert f"| {code.value} |" in contract
    assert REJECT_CODE_CONTRACT_VERSION in contract
    listed = {
        line.split("|")[1].strip() for line in contract.splitlines() if line.startswith("| ") and line.endswith(" |")
    }
    assert listed - {"코드", "---"} == {item.value for item in ProductRejectCode}


@pytest.mark.parametrize(
    "changes",
    [
        {"version": None},
        {"version": "source-reject-codes@2"},
        {"version": "SYNTHETIC_SECRET"},
        {"code": "SYNTHETIC_SECRET"},
        {"code": "UNKNOWN"},
        {"code": "MISSING_ITEM_SEQ"},
        {"location": "page[0].record[0]"},
        {"location": "page[1].record[-1]"},
        {"location": "page[1].record[0].ITEM_SEQ"},
        {"location": "page[1].records[0]"},
        {"location": "page[1].record[0]\n"},
        {"location": "SYNTHETIC_SECRET"},
        {"location": None},
        *[
            {"identity": replace(PRODUCT_REJECT_IDENTITY, **{field: "OTHER"})}
            for field in ("source_code", "endpoint_code", "operation_code")
        ],
    ],
)
def test_unknown_or_malformed_contract_is_safe(changes):
    with pytest.raises(RejectContractError) as error:
        validate(**changes)
    assert str(error.value) == "Source reject contract validation failed."
    assert "SYNTHETIC_SECRET" not in repr(error.value)


def test_parser_version_is_bound_to_contract():
    validate_parser_contract(
        identity=PRODUCT_REJECT_IDENTITY,
        version=REJECT_CODE_CONTRACT_VERSION,
        parser_version=PRODUCT_REJECT_PARSER_VERSION,
    )
    with pytest.raises(RejectContractError):
        validate_parser_contract(
            identity=PRODUCT_REJECT_IDENTITY, version=REJECT_CODE_CONTRACT_VERSION, parser_version="legacy-parser"
        )
