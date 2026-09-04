import json

import pytest

from ai_worker.tasks.rag.source_client.decoders import (
    decode_mfds_json,
)


def _decode(payload: object):
    return decode_mfds_json(
        json.dumps(payload).encode("utf-8"),
        "application/json",
    )


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
