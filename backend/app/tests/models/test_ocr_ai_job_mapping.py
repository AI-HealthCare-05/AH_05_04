from typing import cast

from sqlalchemy import Table, UniqueConstraint

from app.models.ocr import OcrJob


def test_ocr_job_ai_job_id_is_nullable_foreign_key() -> None:
    ai_job_id = OcrJob.__table__.c.ai_job_id

    assert ai_job_id.nullable is True

    foreign_keys = list(ai_job_id.foreign_keys)
    assert len(foreign_keys) == 1

    foreign_key = foreign_keys[0]
    assert foreign_key.target_fullname == "ai_job.id"
    assert foreign_key.ondelete == "SET NULL"
    assert foreign_key.name == "fk_ocr_job_ai_job"


def test_ocr_job_ai_job_id_is_unique() -> None:
    ocr_job_table = cast(Table, OcrJob.__table__)

    unique_constraint_names = {
        constraint.name for constraint in ocr_job_table.constraints if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_ocr_job_ai_job" in unique_constraint_names
