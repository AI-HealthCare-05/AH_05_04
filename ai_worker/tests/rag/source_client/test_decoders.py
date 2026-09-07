import json
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.decoders import (
    decode_mfds_json,
)


def _decode(payload: object):
    return decode_mfds_json(
        json.dumps(payload).encode("utf-8"),
        "application/json",
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected_identity_field"),
    (
        ("list_approved_products_success.json", "ITEM_SEQ"),
        ("list_ingredient_contraindications_success.json", "INGR_CODE"),
        ("list_patient_medication_guides_success.json", "itemSeq"),
    ),
)
def test_decodes_endpoint_specific_sanitized_fixture(
    fixture_name: str,
    expected_identity_field: str,
) -> None:
    fixture_path = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "rag" / "mfds" / fixture_name

    decoded = decode_mfds_json(
        fixture_path.read_bytes(),
        "application/json",
    )

    assert decoded.body_code == "00"
    assert decoded.page_number == 1
    assert decoded.page_size == 100
    assert decoded.total_count == 1
    assert len(decoded.records) == 1
    assert expected_identity_field in decoded.records[0]


def test_decodes_direct_items_array() -> None:
    decoded = _decode(
        {
            "header": {"resultCode": "00"},
            "body": {
                "items": [
                    {
                        "ITEM_SEQ": "product-001",
                        "ITEM_NAME": "합성 의약품",
                    }
                ],
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 1,
            },
        }
    )

    assert decoded.body_code == "00"
    assert decoded.total_count == 1
    assert decoded.records == (
        {
            "ITEM_SEQ": "product-001",
            "ITEM_NAME": "합성 의약품",
        },
    )


def test_decodes_item_wrapped_array_entries() -> None:
    decoded = _decode(
        {
            "header": {"resultCode": "00"},
            "body": {
                "items": [
                    {
                        "item": {
                            "DUR_SEQ": "dur-001",
                            "MIXTURE_DUR_SEQ": "dur-002",
                        }
                    },
                    {
                        "item": {
                            "DUR_SEQ": "dur-003",
                            "MIXTURE_DUR_SEQ": "dur-004",
                        }
                    },
                ],
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 2,
            },
        }
    )

    assert decoded.body_code == "00"
    assert decoded.total_count == 2
    assert decoded.records == (
        {
            "DUR_SEQ": "dur-001",
            "MIXTURE_DUR_SEQ": "dur-002",
        },
        {
            "DUR_SEQ": "dur-003",
            "MIXTURE_DUR_SEQ": "dur-004",
        },
    )


def test_decodes_wrapped_single_item() -> None:
    decoded = _decode(
        {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": {
                            "ITEM_SEQ": "product-001",
                        }
                    },
                    "pageNo": 1,
                    "numOfRows": 100,
                    "totalCount": 1,
                },
            }
        }
    )

    assert decoded.body_code == "00"
    assert decoded.total_count == 1
    assert decoded.records == ({"ITEM_SEQ": "product-001"},)


def test_decodes_empty_items() -> None:
    decoded = _decode(
        {
            "header": {"resultCode": "00"},
            "body": {
                "items": None,
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 0,
            },
        }
    )

    assert decoded.records == ()
    assert decoded.total_count == 0


def test_rejects_non_integer_total_count() -> None:
    with pytest.raises(TypeError, match="totalCount"):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": {
                    "items": [],
                    "pageNo": 1,
                    "numOfRows": 100,
                    "totalCount": "1",
                },
            }
        )


def test_rejects_wrong_media_type() -> None:
    with pytest.raises(ValueError, match="media type"):
        decode_mfds_json(b"{}", "application/xml")


@pytest.mark.parametrize(
    "body",
    (
        b'{"header":{"resultCode":"00","resultCode":"99"},'
        b'"body":{"items":[],"pageNo":1,"numOfRows":100,"totalCount":0}}',
        b'{"header":{"resultCode":"00"},"body":{"items":['
        b'{"ITEM_SEQ":"product-001","ITEM_NAME":"first",'
        b'"ITEM_NAME":"second"}],"pageNo":1,"numOfRows":100,"totalCount":1}}',
    ),
)
def test_rejects_duplicate_object_keys_at_any_depth(body: bytes) -> None:
    with pytest.raises(ValueError, match="duplicate object key"):
        decode_mfds_json(body, "application/json")


def test_rejects_unlisted_response_body_envelope_fields() -> None:
    with pytest.raises(ValueError, match="unsupported envelope field"):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": {
                    "items": [],
                    "pageNo": 1,
                    "numOfRows": 100,
                    "totalCount": 0,
                    "generatedAt": "2099-01-01T00:00:00Z",
                },
            }
        )


@pytest.mark.parametrize(
    "missing_field",
    ("items", "pageNo", "numOfRows", "totalCount"),
)
def test_rejects_missing_required_response_body_fields(
    missing_field: str,
) -> None:
    body: dict[str, object] = {
        "items": [],
        "pageNo": 1,
        "numOfRows": 100,
        "totalCount": 0,
    }
    del body[missing_field]

    with pytest.raises(KeyError):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": body,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("pageNo", "1"),
        ("pageNo", True),
        ("numOfRows", "100"),
        ("numOfRows", True),
        ("totalCount", "0"),
        ("totalCount", True),
    ),
)
def test_rejects_non_integer_pagination_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    body: dict[str, object] = {
        "items": [],
        "pageNo": 1,
        "numOfRows": 100,
        "totalCount": 0,
    }
    body[field_name] = invalid_value

    with pytest.raises(TypeError, match=field_name):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": body,
            }
        )


def test_decodes_page_size_without_inferring_request_equality() -> None:
    decoded = _decode(
        {
            "header": {"resultCode": "00"},
            "body": {
                "items": [],
                "pageNo": 1,
                "numOfRows": 50,
                "totalCount": 0,
            },
        }
    )

    assert decoded.page_size == 50


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("pageNo", 0),
        ("totalCount", -1),
    ),
)
def test_rejects_invalid_pagination_count_boundaries(
    field_name: str,
    invalid_value: int,
) -> None:
    body: dict[str, object] = {
        "items": [],
        "pageNo": 1,
        "numOfRows": 100,
        "totalCount": 0,
    }
    body[field_name] = invalid_value

    with pytest.raises(ValueError, match=field_name):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": body,
            }
        )


def test_rejects_items_wrapper_without_exact_item_field() -> None:
    with pytest.raises(ValueError, match="items wrapper"):
        _decode(
            {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {},
                    "pageNo": 1,
                    "numOfRows": 100,
                    "totalCount": 0,
                },
            }
        )


@pytest.mark.parametrize("constant", (b"NaN", b"Infinity", b"-Infinity"))
def test_rejects_nonstandard_json_numeric_constants(
    constant: bytes,
) -> None:
    body = (
        b'{"header":{"resultCode":"00"},"body":{"items":['
        b'{"ITEM_SEQ":"product-001","value":' + constant + b'},"pageNo":1,"numOfRows":100,"totalCount":1}}'
    )

    with pytest.raises(ValueError, match="non-standard numeric constant"):
        decode_mfds_json(body, "application/json")
