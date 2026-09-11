"""Versioned product rejection contract; no raw values in errors or diagnostics."""

import re
from enum import StrEnum

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity

REJECT_CODE_CONTRACT_VERSION = "source-reject-codes@1"
PRODUCT_REJECT_PARSER_VERSION = "mfds-product-reject-parser@1"
PRODUCT_REJECT_IDENTITY = SourceOperationIdentity(
    source_code="MFDS_PRODUCT_APPROVAL",
    endpoint_code="MFDS_PRODUCT_APPROVAL_API",
    operation_code="LIST_APPROVED_PRODUCTS",
)
_LOCATION = re.compile(r"page\[([0-9]+)\]\.record\[([0-9]+)\]")


class ProductRejectCode(StrEnum):
    ITEM_SEQ_REQUIRED = "ITEM_SEQ_REQUIRED"
    INVALID_ITEM_SEQ_TYPE = "INVALID_ITEM_SEQ_TYPE"
    DUPLICATE_ITEM_SEQ = "DUPLICATE_ITEM_SEQ"


class RejectContractError(ValueError):
    """Safe parser contract failure, including unknown code/version/scope."""

    def __init__(self) -> None:
        super().__init__("Source reject contract validation failed.")


def validate_reject_contract(*, identity: SourceOperationIdentity, version: str | None) -> None:
    if identity != PRODUCT_REJECT_IDENTITY or version != REJECT_CODE_CONTRACT_VERSION:
        raise RejectContractError()


def validate_parser_contract(*, identity: SourceOperationIdentity, version: str | None, parser_version: str) -> None:
    validate_reject_contract(identity=identity, version=version)
    if parser_version != PRODUCT_REJECT_PARSER_VERSION:
        raise RejectContractError()


def validate_parser_location(location: str | None) -> None:
    if not isinstance(location, str) or len(location) > 255:
        raise RejectContractError()
    match = _LOCATION.fullmatch(location)
    if match is None or int(match[1]) < 1:
        raise RejectContractError()


def validate_reject_artifact(
    *, identity: SourceOperationIdentity, version: str | None, code: str | None, location: str | None
) -> None:
    validate_reject_contract(identity=identity, version=version)
    if not isinstance(code, str) or code not in ProductRejectCode:
        raise RejectContractError()
    validate_parser_location(location)


def parser_location_identity(location: str | None) -> tuple[int, int]:
    """Numeric identity prevents spelling variants from counting the same record twice."""
    validate_parser_location(location)
    assert location is not None
    match = _LOCATION.fullmatch(location)
    assert match is not None
    return int(match[1]), int(match[2])
