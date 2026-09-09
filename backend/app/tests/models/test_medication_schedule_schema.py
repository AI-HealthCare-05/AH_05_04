from typing import cast

from sqlalchemy import DateTime, Table, UniqueConstraint

from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleStatus,
    MedicationScheduleTime,
)


def test_track_b_status_enums_match_approved_contract() -> None:
    assert set(MedicationScheduleEndMode) == {"DATE", "OPEN_ENDED"}
    assert set(MedicationScheduleSource) == {"PRESCRIPTION_EXACT", "USER_CONFIRMED"}
    assert set(MedicationScheduleStatus) == {"ACTIVE", "CANCELLED", "ENDED"}
    assert set(MedicationOccurrenceStatus) == {"PENDING", "CANCELLED", "CLOSED"}


def test_schedule_references_one_stable_version_medication() -> None:
    table = cast(Table, MedicationSchedule.__table__)
    column = table.c.prescription_version_medication_id

    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {
        "prescription_version_medication.id"
    }
    assert any(
        isinstance(constraint, UniqueConstraint)
        and set(constraint.columns.keys()) == {"prescription_version_medication_id"}
        for constraint in table.constraints
    )


def test_schedule_time_and_occurrence_have_independent_duplicate_guards() -> None:
    schedule_time_table = cast(Table, MedicationScheduleTime.__table__)
    occurrence_table = cast(Table, MedicationOccurrence.__table__)

    schedule_time_unique = next(
        constraint
        for constraint in schedule_time_table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_medication_schedule_time_revision_local_time"
    )
    occurrence_unique = next(
        constraint
        for constraint in occurrence_table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name == "uq_medication_occurrence_time_local_date"
    )

    assert list(schedule_time_unique.columns.keys()) == [
        "medication_schedule_id",
        "schedule_revision",
        "local_time",
    ]
    assert list(occurrence_unique.columns.keys()) == ["medication_schedule_time_id", "scheduled_local_date"]


def test_occurrence_instants_are_timezone_aware_columns() -> None:
    for name in ("scheduled_at", "confirmation_deadline_at"):
        column_type = MedicationOccurrence.__table__.c[name].type
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True
