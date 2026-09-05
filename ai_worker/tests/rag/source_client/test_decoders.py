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
                    "totalCount": "1",
                },
            }
        )


def test_rejects_wrong_media_type() -> None:
    with pytest.raises(ValueError, match="media type"):
        decode_mfds_json(b"{}", "application/xml")
