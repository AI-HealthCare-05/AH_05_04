import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.security import get_request_user
from app.dtos.lifestyle_times import LifestyleTimesResponse, PutLifestyleTimesRequest
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.lifestyle_times import LifestyleTimes
from app.tests.medication_checkins.test_medication_checkin_api import assert_error
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


@dataclass
class Case:
    client: AsyncClient
    session: AsyncSession
    user_id: UUID

    @property
    def body(self) -> dict:
        return {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [
                        {
                            "kind": "DINNER",
                            "pattern": "NOT_USUALLY_EATEN",
                            "window": None,
                        },
                        {
                            "kind": "BREAKFAST",
                            "pattern": "REGULAR",
                            "window": {
                                "start_local_time": "07:30",
                                "end_local_time": "08:00",
                                "end_day_offset": 0,
                            },
                        },
                        {"kind": "LUNCH", "pattern": "IRREGULAR", "window": None},
                    ],
                    "anchors": [
                        {"kind": "LEAVE_HOME", "local_time": "08:30"},
                        {"kind": "BEDTIME", "local_time": "23:30"},
                    ],
                    "unavailable_windows": [
                        {
                            "start_local_time": "23:00",
                            "end_local_time": "01:00",
                            "end_day_offset": 1,
                        },
                        {
                            "start_local_time": "09:00",
                            "end_local_time": "12:00",
                            "end_day_offset": 0,
                        },
                    ],
                }
            ],
        }

    async def get(self) -> Response:
        return await self.client.get("/api/v1/lifestyle-times")

    async def put(self, body: dict, *, key: str | None = "lifestyle-first-key") -> Response:
        return await self.client.put(
            "/api/v1/lifestyle-times",
            json=body,
            headers={"Idempotency-Key": key} if key else {},
        )


@pytest.fixture
async def case(db_session: AsyncSession) -> AsyncIterator[Case]:
    owner, _ = await _create_user_with_self_profile(db_session, label="lifestyle-api")
    await db_session.commit()
    owner_id = owner.id
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield Case(client=client, session=db_session, user_id=owner_id)
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


async def test_unstored_create_normalize_replay_update_and_reset(case: Case) -> None:
    unstored = await case.get()
    assert unstored.status_code == 200
    assert unstored.json()["data"] == {
        "revision": 0,
        "updated_at": None,
        "timezone": "Asia/Seoul",
        "days": [],
    }
    assert unstored.headers["cache-control"] == "no-store"

    created = await case.put(case.body)
    assert created.status_code == 200, created.text
    parsed = LifestyleTimesResponse.model_validate(created.json())
    assert parsed.data.revision == 1
    assert parsed.data.updated_at is not None
    assert [meal.kind for meal in parsed.data.days[0].meals] == ["BREAKFAST", "LUNCH", "DINNER"]
    assert [anchor.kind for anchor in parsed.data.days[0].anchors] == ["BEDTIME", "LEAVE_HOME"]
    assert [window.start_local_time for window in parsed.data.days[0].unavailable_windows] == ["09:00", "23:00"]

    replay = await case.put(case.body)
    assert replay.json() == created.json()
    assert await case.session.scalar(select(func.count()).select_from(LifestyleTimes)) == 1
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1

    reordered = {
        **case.body,
        "days": [
            {
                **case.body["days"][0],
                "meals": list(reversed(case.body["days"][0]["meals"])),
                "anchors": list(reversed(case.body["days"][0]["anchors"])),
                "unavailable_windows": list(reversed(case.body["days"][0]["unavailable_windows"])),
            }
        ],
    }
    assert (await case.put(reordered)).json() == created.json()

    same_content = await case.put(
        {**case.body, "expected_revision": 1},
        key="lifestyle-same-content-new-key",
    )
    assert same_content.status_code == 200
    assert same_content.json()["data"]["revision"] == 2

    reset = await case.put({"expected_revision": 2, "days": []}, key="lifestyle-reset-key")
    assert reset.status_code == 200
    assert reset.json()["data"]["revision"] == 3
    assert reset.json()["data"]["days"] == []
    assert (await case.get()).json() == reset.json()


async def test_revision_and_idempotency_conflicts(case: Case) -> None:
    assert (await case.put(case.body)).status_code == 200
    assert_error(
        await case.put(case.body, key="lifestyle-stale-key"),
        409,
        "LIFESTYLE_TIMES_REVISION_CONFLICT",
    )
    changed = {**case.body, "days": []}
    assert_error(await case.put(changed), 409, "IDEMPOTENCY_KEY_CONFLICT")


async def test_snapshot_failure_rolls_back_current_state(case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import idempotency

    monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
    assert_error(await case.put(case.body), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert await case.session.scalar(select(func.count()).select_from(LifestyleTimes)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_put_does_not_change_medication_schedule_notification_or_checkin(case: Case) -> None:
    from datetime import UTC, date, datetime, time, timedelta

    from app.models.medication_schedules import (
        MedicationCheckin,
        MedicationCheckinStatus,
        MedicationScheduleEndMode,
        MedicationScheduleSource,
    )
    from app.models.notifications import NotificationKind, NotificationRecord, NotificationStatus
    from app.models.profiles import Profile
    from app.models.users import User
    from app.repositories.medication_schedule_repository import MedicationScheduleRepository
    from app.tests.repositories.test_medication_schedule_repository_integration import (
        _create_active_version_medication,
    )

    owner = await case.session.get(User, case.user_id)
    profile = await case.session.scalar(select(Profile).where(Profile.user_id == case.user_id))
    assert owner is not None and profile is not None
    _, medication = await _create_active_version_medication(case.session, owner=owner, profile=profile)
    repository = MedicationScheduleRepository(case.session)
    schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=case.user_id,
        start_local_date=date(2026, 9, 15),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    schedule_time = (
        await repository.add_schedule_times(schedule=schedule, schedule_revision=1, local_times=[time(9, 0)])
    )[0]
    scheduled_at = datetime(2026, 9, 15, tzinfo=UTC)
    occurrence = await repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2026, 9, 15),
        scheduled_at=scheduled_at,
        confirmation_deadline_at=scheduled_at + timedelta(hours=4),
    )
    checkin = MedicationCheckin(
        occurrence_id=occurrence.id,
        status=MedicationCheckinStatus.UNCONFIRMED,
        revision=1,
    )
    notification = NotificationRecord(
        occurrence_id=occurrence.id,
        kind=NotificationKind.SCHEDULED,
        scheduled_at=scheduled_at,
        status=NotificationStatus.PENDING,
        attempt=0,
    )
    case.session.add_all([checkin, notification])
    await case.session.commit()
    before = {
        "schedule": (schedule.status, schedule.revision),
        "occurrence": (occurrence.status, occurrence.cancelled_at),
        "checkin": (checkin.status, checkin.revision),
        "notification": (notification.status, notification.attempt),
    }
    schedule_id = schedule.id
    occurrence_id = occurrence.id
    checkin_id = checkin.id
    notification_id = notification.id

    assert (await case.put(case.body)).status_code == 200
    case.session.expire_all()
    stored_schedule = await case.session.get(type(schedule), schedule_id)
    stored_occurrence = await case.session.get(type(occurrence), occurrence_id)
    stored_checkin = await case.session.get(type(checkin), checkin_id)
    stored_notification = await case.session.get(type(notification), notification_id)
    assert stored_schedule is not None
    assert stored_occurrence is not None
    assert stored_checkin is not None
    assert stored_notification is not None
    assert before == {
        "schedule": (stored_schedule.status, stored_schedule.revision),
        "occurrence": (stored_occurrence.status, stored_occurrence.cancelled_at),
        "checkin": (stored_checkin.status, stored_checkin.revision),
        "notification": (stored_notification.status, stored_notification.attempt),
    }


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True, "days": []},
        {"expected_revision": 0, "days": [], "profile_id": "00000000-0000-0000-0000-000000000000"},
        {"expected_revision": 0, "days": None},
        {
            "expected_revision": 0,
            "days": [
                {"weekday": weekday, "meals": [], "anchors": [], "unavailable_windows": []} for weekday in range(1, 9)
            ],
        },
        {
            "expected_revision": 0,
            "days": [{"weekday": True, "meals": [], "anchors": [], "unavailable_windows": []}],
        },
        {"expected_revision": 0, "days": [{"weekday": 1, "meals": [], "anchors": [], "unavailable_windows": []}] * 2},
        {
            "expected_revision": 0,
            "days": [{"weekday": 1, "meals": None, "anchors": [], "unavailable_windows": []}],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [
                        {"kind": "BREAKFAST", "pattern": "IRREGULAR", "window": None},
                        {"kind": "BREAKFAST", "pattern": "NOT_USUALLY_EATEN", "window": None},
                    ],
                    "anchors": [],
                    "unavailable_windows": [],
                }
            ],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [],
                    "anchors": [
                        {"kind": "WAKE_UP", "local_time": "07:00"},
                        {"kind": "WAKE_UP", "local_time": "08:00"},
                    ],
                    "unavailable_windows": [],
                }
            ],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [{"kind": "BREAKFAST", "pattern": "REGULAR", "window": None}],
                    "anchors": [],
                    "unavailable_windows": [],
                }
            ],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [],
                    "anchors": [],
                    "unavailable_windows": [
                        {"start_local_time": "09:00", "end_local_time": "09:00", "end_day_offset": 0}
                    ],
                }
            ],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [],
                    "anchors": [],
                    "unavailable_windows": [
                        {"start_local_time": "09:00", "end_local_time": "10:00", "end_day_offset": 1}
                    ],
                }
            ],
        },
        {
            "expected_revision": 0,
            "days": [
                {
                    "weekday": 1,
                    "meals": [],
                    "anchors": [],
                    "unavailable_windows": [
                        {"start_local_time": "09:00", "end_local_time": "10:00", "end_day_offset": True}
                    ],
                }
            ],
        },
    ],
)
async def test_invalid_payload_is_rejected_without_storage(case: Case, body: dict) -> None:
    assert_error(await case.put(body), 422, "VALIDATION_FAILED")
    assert await case.session.scalar(select(func.count()).select_from(LifestyleTimes)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_key_auth_and_missing_self_profile(case: Case) -> None:
    assert_error(await case.put(case.body, key=None), 400, "IDEMPOTENCY_KEY_REQUIRED")
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await case.get()).status_code == 401
    assert (await case.put(case.body)).status_code == 401

    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=UUID(int=0))
    assert_error(await case.get(), 404, "LIFESTYLE_TIMES_NOT_FOUND")
    assert_error(await case.put(case.body), 404, "LIFESTYLE_TIMES_NOT_FOUND")


async def test_users_only_read_and_replace_their_own_self_profile(case: Case) -> None:
    assert (await case.put(case.body)).status_code == 200
    other, _ = await _create_user_with_self_profile(case.session, label="lifestyle-other")
    await case.session.commit()
    other_id = other.id
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=other_id)

    other_empty = await case.get()
    assert other_empty.status_code == 200
    assert other_empty.json()["data"]["revision"] == 0
    assert other_empty.json()["data"]["days"] == []
    other_created = await case.put({"expected_revision": 0, "days": []}, key="lifestyle-other-key")
    assert other_created.status_code == 200

    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=case.user_id)
    owner = await case.get()
    assert owner.json()["data"]["revision"] == 1
    assert owner.json()["data"]["days"]


def test_openapi_lifestyle_times_contract() -> None:
    schema = fastapi_app.openapi()
    path = schema["paths"]["/api/v1/lifestyle-times"]
    assert path["get"]["operationId"] == "lifestyle-times.get"
    put = path["put"]
    assert put["operationId"] == "lifestyle-times.put"
    assert next(parameter for parameter in put["parameters"] if parameter["name"] == "Idempotency-Key")["required"]
    for status in (400, 401, 404, 409, 422, 503):
        assert put["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    request_schema = schema["components"]["schemas"]["PutLifestyleTimesRequest"]
    assert set(request_schema["required"]) == {"expected_revision", "days"}
    assert request_schema["additionalProperties"] is False


def test_request_model_preserves_overlaps_and_rejects_exact_duplicates() -> None:
    overlap = {
        "expected_revision": 0,
        "days": [
            {
                "weekday": 7,
                "meals": [],
                "anchors": [],
                "unavailable_windows": [
                    {"start_local_time": "22:00", "end_local_time": "01:00", "end_day_offset": 1},
                    {"start_local_time": "23:00", "end_local_time": "00:30", "end_day_offset": 1},
                ],
            }
        ],
    }
    assert len(PutLifestyleTimesRequest.model_validate(overlap).days[0].unavailable_windows) == 2
    overlap["days"][0]["unavailable_windows"].append(
        {"start_local_time": "22:00", "end_local_time": "01:00", "end_day_offset": 1}
    )
    with pytest.raises(ValueError, match="duplicate unavailable window"):
        PutLifestyleTimesRequest.model_validate(overlap)


def test_frontend_synthetic_fixtures_match_dtos() -> None:
    fixtures = json.loads(
        (
            Path(__file__).resolve().parents[4] / "docs/validation/track-b/issue-556-lifestyle-times-fixtures.json"
        ).read_text()
    )
    PutLifestyleTimesRequest.model_validate(fixtures["put_request"])
    LifestyleTimesResponse.model_validate(fixtures["put_response"])
    LifestyleTimesResponse.model_validate(fixtures["unstored_response"])


async def test_concurrent_puts_replay_same_key_and_serialize_updates() -> None:
    from app.core.db.databases import get_db_session
    from app.models.profiles import Profile
    from app.models.users import User
    from app.tests.conftest import test_engine

    async with AsyncSession(test_engine, expire_on_commit=False) as seed:
        owner, profile = await _create_user_with_self_profile(seed, label="lifestyle-race")
        await seed.commit()
        owner_id = owner.id
        profile_id = profile.id

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(test_engine, expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    body = {"expected_revision": 0, "days": []}
    previous_session = fastapi_app.dependency_overrides.get(get_db_session)
    fastapi_app.dependency_overrides[get_db_session] = request_session
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

            async def send(payload: dict, key: str) -> Response:
                return await client.put(
                    "/api/v1/lifestyle-times",
                    json=payload,
                    headers={"Idempotency-Key": key},
                )

            first, second = await asyncio.wait_for(
                asyncio.gather(
                    send(body, "concurrent-lifestyle-key"),
                    send(body, "concurrent-lifestyle-key"),
                ),
                timeout=15,
            )
            update_a = {"expected_revision": 1, "days": []}
            update_b = {
                "expected_revision": 1,
                "days": [{"weekday": 1, "meals": [], "anchors": [], "unavailable_windows": []}],
            }
            third, fourth = await asyncio.wait_for(
                asyncio.gather(
                    send(update_a, "concurrent-update-a"),
                    send(update_b, "concurrent-update-b"),
                ),
                timeout=15,
            )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert sorted((third.status_code, fourth.status_code)) == [200, 409]
        conflict = third if third.status_code == 409 else fourth
        assert_error(conflict, 409, "LIFESTYLE_TIMES_REVISION_CONFLICT")
        async with AsyncSession(test_engine, expire_on_commit=False) as verify:
            assert (
                await verify.scalar(
                    select(func.count()).select_from(LifestyleTimes).where(LifestyleTimes.profile_id == profile_id)
                )
                == 1
            )
            assert (
                await verify.scalar(
                    select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.user_id == owner_id)
                )
                == 2
            )
            stored = await verify.get(LifestyleTimes, profile_id)
            assert stored is not None and stored.revision == 2
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        if previous_session is not None:
            fastapi_app.dependency_overrides[get_db_session] = previous_session
        else:
            fastapi_app.dependency_overrides.pop(get_db_session, None)
        async with AsyncSession(test_engine, expire_on_commit=False) as cleanup:
            await cleanup.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == owner_id))
            await cleanup.execute(delete(LifestyleTimes).where(LifestyleTimes.profile_id == profile_id))
            await cleanup.execute(delete(Profile).where(Profile.id == profile_id))
            await cleanup.execute(delete(User).where(User.id == owner_id))
            await cleanup.commit()
