"""Python 처방 내용 hash v1. DB 및 Worker에서 공유하며 원문을 오류에 넣지 않습니다."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

PRESCRIPTION_CONTENT_SPEC = "prescription-content@1"
MEDICATION_CONTENT_FIELDS = (
    "display_order",
    "medication_name",
    "strength_text",
    "dose_value",
    "dose_unit",
    "frequency_per_day",
    "timing_text",
    "duration_days",
)


@dataclass(frozen=True)
class PrescriptionFingerprint:
    medication_count: int
    content_hash: str


def prescription_fingerprint(
    prescribed_date: date, medications: Sequence[Mapping[str, Any]]
) -> PrescriptionFingerprint:
    if type(prescribed_date) is not date or not medications:
        raise ValueError("Prescription date and at least one medication are required")
    rows: list[dict[str, Any]] = [{key: item.get(key) for key in MEDICATION_CONTENT_FIELDS} for item in medications]
    orders = [row["display_order"] for row in rows]
    if any(type(order) is not int for order in orders) or set(orders) != set(range(1, len(rows) + 1)):
        raise ValueError("Prescription medication slots must be exactly 1 through medication count")
    for row in rows:
        _validate_row(row)
    payload = {
        "spec": PRESCRIPTION_CONTENT_SPEC,
        "prescribed_date": prescribed_date.isoformat(),
        "medications": sorted(rows, key=lambda row: row["display_order"]),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return PrescriptionFingerprint(len(rows), hashlib.sha256(encoded).hexdigest())


def _validate_row(row: dict[str, Any]) -> None:
    name = row["medication_name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise ValueError("Invalid prescription medication name")
    for key, limit in (("strength_text", 100), ("dose_unit", 50), ("timing_text", 255)):
        value = row[key]
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError("Invalid prescription medication text")
    for key in ("frequency_per_day", "duration_days"):
        value = row[key]
        if value is not None and (type(value) is not int or not 0 < value <= 2_147_483_647):
            raise ValueError("Invalid prescription medication integer")
    value = row["dose_value"]
    if value is not None:
        try:
            dose = Decimal(str(value))
            if (
                not dose.is_finite()
                or not 0 < dose <= Decimal("9999999.999")
                or dose != dose.quantize(Decimal("0.001"))
            ):
                raise ValueError("Invalid prescription medication dose")
            row["dose_value"] = format(dose, ".3f")
        except InvalidOperation:
            raise ValueError("Invalid prescription medication dose") from None


def verify_prescription_fingerprint(
    prescribed_date: date,
    medications: Sequence[Mapping[str, Any]],
    *,
    medication_count: int | None,
    content_hash: str | None,
) -> None:
    """봉인 metadata 누락·구성 변조를 fail-closed로 거부합니다."""
    if type(medication_count) is not int or medication_count < 1 or not isinstance(content_hash, str):
        raise ValueError("Prescription integrity metadata is missing")
    if any(row.get("medication_count") != medication_count for row in medications):
        raise ValueError("Prescription medication count binding is invalid")
    actual = prescription_fingerprint(prescribed_date, medications)
    if actual.medication_count != medication_count or actual.content_hash != content_hash:
        raise ValueError("Prescription integrity verification failed")
