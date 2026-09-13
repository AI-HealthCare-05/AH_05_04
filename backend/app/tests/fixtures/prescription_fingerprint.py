"""명시된 합성 약 목록의 정상 fingerprint를 생성하는 테스트 helper."""

from dataclasses import asdict
from datetime import date

from provider_contracts.prescription_integrity import prescription_fingerprint


def fingerprint_values(prescribed_date: date, medications: list[dict]) -> dict:
    return asdict(prescription_fingerprint(prescribed_date, medications))
