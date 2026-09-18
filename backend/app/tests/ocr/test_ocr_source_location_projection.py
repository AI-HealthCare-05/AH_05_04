"""#809 REQ-OCR-014 추출 근거 위치의 응답 투영 검증."""

from decimal import Decimal
from uuid import uuid4

from app.models.ocr import ConfirmationStatus, ExtractedField, FieldType
from app.services.ocr import _to_field_data


def _field(**overrides: object) -> ExtractedField:
    values: dict = {
        "id": uuid4(),
        "ocr_job_id": uuid4(),
        "medication_index": 1,
        "field_type": FieldType.MEDICATION_NAME,
        "raw_value": "타이레놀정500mg",
        "normalized_value": "타이레놀정",
        "normalization_version": "name-rule-v1",
        "confirmed_value": None,
        "confidence_score": Decimal("0.9900"),
        "confirmation_status": ConfirmationStatus.UNCONFIRMED,
        "source_page": 1,
        "source_bbox_x": Decimal("137.00"),
        "source_bbox_y": Decimal("627.00"),
        "source_bbox_width": Decimal("200.00"),
        "source_bbox_height": Decimal("20.00"),
    }
    values.update(overrides)
    return ExtractedField(**values)


def test_complete_source_location_is_projected_as_page_and_bbox() -> None:
    data = _to_field_data(_field())

    assert data.source_location is not None
    assert data.source_location.page == 1
    assert data.source_location.bbox == (137.0, 627.0, 200.0, 20.0)


def test_missing_source_location_is_projected_as_none() -> None:
    data = _to_field_data(
        _field(
            source_page=None,
            source_bbox_x=None,
            source_bbox_y=None,
            source_bbox_width=None,
            source_bbox_height=None,
        )
    )

    assert data.source_location is None


def test_partial_source_location_is_not_projected() -> None:
    """DB CHECK가 막는 조합이지만 응답 계층도 부분 좌표를 노출하지 않는다."""
    data = _to_field_data(_field(source_bbox_width=None))

    assert data.source_location is None
