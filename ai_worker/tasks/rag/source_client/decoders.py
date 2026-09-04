"""Strict decoders for documented MFDS JSON response envelopes."""

import json
from collections.abc import Mapping
from typing import cast

from ai_worker.tasks.rag.source_client.mfds_client import (
    DecodedProviderPage,
)


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("MFDS response object was expected.")

    if not all(isinstance(key, str) for key in value):
        raise TypeError("MFDS response keys must be strings.")

    return cast(dict[str, object], value)


def _decode_records(raw_items: object) -> tuple[Mapping[str, object], ...]:
    if raw_items is None:
        return ()

    # 일부 공공데이터 JSON 응답은 body.items를 바로 배열로 반환합니다.
    if isinstance(raw_items, list):
        raw_records: list[object] = raw_items
    else:
        # Swagger 모델처럼 body.items.item으로 감싼 형태도 처리합니다.
        item_container = _require_object(raw_items)
        raw_item = item_container.get("item")

        if raw_item is None:
            return ()

        if isinstance(raw_item, list):
            raw_records = raw_item
        elif isinstance(raw_item, dict):
            raw_records = [raw_item]
        else:
            raise TypeError("MFDS item collection has an invalid type.")

    return tuple(_require_object(record) for record in raw_records)


def decode_mfds_json(
    body: bytes,
    media_type: str,
) -> DecodedProviderPage:
    """Decode the common MFDS header/body JSON envelope."""

    if media_type != "application/json":
        raise ValueError("MFDS JSON decoder received another media type.")

    payload: object = json.loads(body)
    root = _require_object(payload)

    # 일부 Gateway 응답은 최상위를 response로 한 번 더 감쌀 수 있습니다.
    response_value = root.get("response", root)
    response = _require_object(response_value)
    header = _require_object(response["header"])
    body_envelope = _require_object(response["body"])

    result_code = header["resultCode"]
    total_count = body_envelope.get("totalCount")

    if not isinstance(result_code, str):
        raise TypeError("MFDS resultCode must be a string.")

    # bool은 int의 하위 타입이므로 명시적으로 제외합니다.
    if total_count is not None and type(total_count) is not int:
        raise TypeError("MFDS totalCount must be an integer.")

    records = _decode_records(body_envelope.get("items"))

    return DecodedProviderPage(
        body_code=result_code,
        records=records,
        total_count=total_count,
    )
