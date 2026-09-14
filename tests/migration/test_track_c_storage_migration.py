"""C1 on a migrated, disposable PostgreSQL DB; synthetic records only."""

import asyncio
import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.models.track_c import (
    ActionPlanFollowup,
    ActionPlanFollowupAudit,
    BarrierResponse,
    SafetyAssessment,
    SupportActionPlan,
    SupportCode,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.track_c_handler_config import (
    HandlerConfigError,
    parse_handler_config,
    restore_action_plan_snapshot,
    save_action_plan_snapshot,
)
from tests.migration.test_medication_schedule_migration import _connection, _seed_graph, create_alembic_config
from tests.services.test_track_c_handler_config import APPROVALS, synthetic_rules

REVISION = "192a1b2c3d4e"
TABLES = [
    model.__table__
    for model in (SafetyAssessment, BarrierResponse, SupportActionPlan, ActionPlanFollowup, ActionPlanFollowupAudit)
]


def _parent_revision() -> str:
    path = Path(__file__).resolve().parents[2] / "backend/alembic/versions/192a1b2c3d4e_track_c_storage.py"
    spec = importlib.util.spec_from_file_location("track_c_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.down_revision


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    database = "track_c192_" + uuid4().hex[:12]
    admin_url = make_url(config.database_url).set(database="postgres")

    async def manage(create: bool):
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)')
                )
        finally:
            await engine.dispose()

    asyncio.run(manage(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(manage(False))


async def _run(sql, values=None):
    async with _connection() as connection:
        async with connection.begin():
            result = await connection.execute(text(sql), values or {})
            return result.scalar() if result.returns_rows else None


async def _seed():
    ids = {
        **await _seed_graph(),
        **{key: str(uuid4()) for key in ("checkin_id", "safety_id", "barrier_id", "plan_id", "followup_id")},
    }
    await _run(
        "INSERT INTO medication_checkin (id, occurrence_id, status, revision) VALUES (:checkin_id, :occurrence_id, 'NOT_TAKEN', 1)",
        ids,
    )
    return ids


SAFETY = """INSERT INTO safety_assessment
(id, medication_checkin_id, checkin_revision, revision, symptom_codes, response_level,
 safety_disposition, message_code, copy_version, source_version)
VALUES (:safety_id, :checkin_id, 1, 1, '[]'::jsonb, 'ROUTINE', 'NORMAL', 'SYNTHETIC', 'synthetic-v1', 'synthetic-v1')"""
BARRIER = """INSERT INTO barrier_response
(id, medication_checkin_id, checkin_revision, safety_assessment_id, revision, response_status, barrier_code)
VALUES (:barrier_id, :checkin_id, 1, :safety_id, 1, 'ANSWERED', 'FORGOT')"""
PLAN = """INSERT INTO support_action_plan
(id, barrier_response_id, support_code, rule_version, copy_version, action_config_snapshot, status)
VALUES (:plan_id, :barrier_id, 'REMINDER_SETUP', 'synthetic-v1', 'synthetic-v1', '{}'::jsonb, 'ACTIVE')"""
FOLLOWUP = """INSERT INTO action_plan_followup (id, support_action_plan_id, response, revision)
VALUES (:followup_id, :plan_id, 'HELPED', 1)"""


async def _seed_all():
    ids = await _seed()
    for statement in (SAFETY, BARRIER, PLAN, FOLLOWUP):
        await _run(statement, ids)
    return ids


def test_fresh_upgrade_metadata_and_no_database_logic():
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")

    async def verify():
        async with _connection() as connection:

            def metadata(sync_connection):
                inspector = inspect(sync_connection)
                for table in TABLES:
                    columns = inspector.get_columns(table.name)
                    assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.c}
                    assert {c["name"] for c in inspector.get_check_constraints(table.name)} == {
                        c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
                    }
                    assert {c["name"] for c in inspector.get_unique_constraints(table.name)} == {
                        c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"
                    }
                    assert {c["name"] for c in inspector.get_foreign_keys(table.name)} == {
                        c.name for c in table.foreign_key_constraints
                    }
                indexes = inspector.get_indexes("support_action_plan")
                active = next(i for i in indexes if i["name"] == "uq_action_plan_active_barrier")
                assert active["unique"]
                assert "ACTIVE" in active["dialect_options"]["postgresql_where"]

            await connection.run_sync(metadata)
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class WHERE relname = ANY(:names))"
                    ),
                    {"names": [t.name for t in TABLES]},
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relname = ANY(:names)"),
                    {"names": [t.name for t in TABLES]},
                )
                == 0
            )

    asyncio.run(verify())
    command.downgrade(cfg, _parent_revision())
    assert asyncio.run(_run("SELECT to_regclass('safety_assessment')")) is None
    command.upgrade(cfg, "head")


def test_develop_upgrade_preserves_parent_data_and_history_blocks_downgrade():
    cfg = create_alembic_config()
    command.upgrade(cfg, _parent_revision())
    ids = asyncio.run(_seed())
    command.upgrade(cfg, "head")
    assert asyncio.run(_run("SELECT revision FROM medication_checkin WHERE id=:checkin_id", ids)) == 1
    asyncio.run(_run(SAFETY, ids))
    # Historical revision must not be an FK to the mutable current Check-in revision.
    asyncio.run(_run("UPDATE medication_checkin SET revision=2, status='TAKEN' WHERE id=:checkin_id", ids))
    assert asyncio.run(_run("SELECT checkin_revision FROM safety_assessment WHERE id=:safety_id", ids)) == 1
    with pytest.raises(RuntimeError, match="history exists"):
        command.downgrade(cfg, _parent_revision())
    assert asyncio.run(_run("SELECT count(*) FROM safety_assessment")) == 1


@pytest.mark.parametrize(
    "statement, constraint",
    [
        (SAFETY.replace("1, 1,", "0, 1,"), "chk_safety_revisions"),
        (SAFETY.replace("'ROUTINE'", "'INVALID'"), "chk_safety_"),
        (SAFETY.replace("'NORMAL'", "'URGENT_ROUTED'"), "chk_safety_disposition"),
        (SAFETY.replace("'[]'::jsonb", "'{}'::jsonb"), "chk_safety_symptoms_array"),
        (SAFETY.replace(":checkin_id", "'00000000-0000-0000-0000-000000000000'"), "fk_safety_checkin"),
    ],
)
def test_invalid_safety_shape_rejected(statement, constraint):
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed())
    with pytest.raises(DBAPIError, match=constraint):
        asyncio.run(_run(statement, ids))
    assert asyncio.run(_run("SELECT count(*) FROM safety_assessment")) == 0


def test_barrier_parent_revision_and_declined_shape():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed())
    asyncio.run(_run(SAFETY, ids))
    for statement, constraint in [
        (BARRIER.replace("'FORGOT'", "NULL"), "chk_barrier_response"),
        (BARRIER.replace("'ANSWERED'", "'DECLINED'"), "chk_barrier_response"),
        (BARRIER.replace("'FORGOT'", "'UNKNOWN'"), "chk_barrier_code"),
        (BARRIER.replace(":checkin_id, 1,", ":checkin_id, 2,"), "fk_barrier_safety_reference"),
    ]:
        with pytest.raises(DBAPIError, match=constraint):
            asyncio.run(_run(statement, ids))
    other = asyncio.run(_seed())
    with pytest.raises(DBAPIError, match="fk_barrier_safety_reference"):
        asyncio.run(_run(BARRIER, {**ids, "checkin_id": other["checkin_id"]}))
    asyncio.run(_run(BARRIER.replace("'ANSWERED', 'FORGOT'", "'DECLINED', NULL"), ids))
    with pytest.raises(DBAPIError, match="uq_barrier_checkin_revision"):
        asyncio.run(_run(BARRIER, {**ids, "barrier_id": str(uuid4())}))


def test_action_plan_active_unique_terminal_history_and_snapshot():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_all())
    with pytest.raises(DBAPIError, match="uq_action_plan_active_barrier"):
        asyncio.run(_run(PLAN, {**ids, "plan_id": str(uuid4())}))
    with pytest.raises(DBAPIError, match="chk_action_plan_state"):
        asyncio.run(_run("UPDATE support_action_plan SET status='COMPLETED' WHERE id=:plan_id", ids))
    asyncio.run(_run("UPDATE support_action_plan SET status='COMPLETED', completed_at=now() WHERE id=:plan_id", ids))
    for bad in ("'[]'::jsonb", "'null'::jsonb"):
        with pytest.raises(DBAPIError, match="chk_action_plan_snapshot"):
            asyncio.run(_run(PLAN.replace("'{}'::jsonb", bad), {**ids, "plan_id": str(uuid4())}))
    asyncio.run(_run(PLAN, {**ids, "plan_id": str(uuid4())}))
    assert asyncio.run(_run("SELECT count(*) FROM support_action_plan")) == 2


def test_followup_unique_audit_shape_and_caller_rollback():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_all())
    with pytest.raises(DBAPIError, match="uq_followup_action_plan"):
        asyncio.run(_run(FOLLOWUP, {**ids, "followup_id": str(uuid4())}))
    audit = """INSERT INTO action_plan_followup_audit
        (id, followup_id, from_response, to_response, from_revision, to_revision, changed_by, changed_at)
        VALUES (:audit_id, :followup_id, 'HELPED', 'NOT_SURE', 1, 2, :user_id, now())"""
    ids["audit_id"] = str(uuid4())
    for statement, constraint in [
        (audit.replace("1, 2,", "1, 3,"), "chk_followup_audit_revision"),
        (audit.replace("'NOT_SURE'", "'LATER'"), "chk_followup_audit_response"),
    ]:
        with pytest.raises(DBAPIError, match=constraint):
            asyncio.run(_run(statement, ids))

    async def correction(rollback):
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(text(audit), ids)
                await connection.execute(
                    text(
                        "UPDATE action_plan_followup SET response='NOT_SURE', revision=2, updated_at=now() WHERE id=:followup_id"
                    ),
                    ids,
                )
                if rollback:
                    raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        asyncio.run(correction(True))
    assert asyncio.run(_run("SELECT count(*) FROM action_plan_followup_audit")) == 0
    assert asyncio.run(_run("SELECT response FROM action_plan_followup")) == "HELPED"
    asyncio.run(correction(False))
    assert asyncio.run(_run("SELECT revision FROM action_plan_followup")) == 2
    assert asyncio.run(_run("SELECT from_response FROM action_plan_followup_audit")) == "HELPED"


def test_owned_reads_hide_foreign_and_missing_ids():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_all())
    other = asyncio.run(_seed())

    async def verify():
        engine = create_async_engine(config.database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session:
                repository = TrackCStorageRepository(session)
                for method, field, key in [
                    (repository.get_safety_owned, "assessment_id", "safety_id"),
                    (repository.get_barrier_owned, "barrier_id", "barrier_id"),
                    (repository.get_action_plan_owned, "plan_id", "plan_id"),
                    (repository.get_followup_owned, "followup_id", "followup_id"),
                ]:
                    assert await method(**{field: UUID(ids[key]), "user_id": UUID(ids["user_id"])}) is not None
                    assert await method(**{field: UUID(ids[key]), "user_id": UUID(other["user_id"])}) is None
                    assert await method(**{field: uuid4(), "user_id": UUID(ids["user_id"])}) is None
        finally:
            await engine.dispose()

    asyncio.run(verify())


def test_handler_snapshot_database_round_trip_and_rejection():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed())
    asyncio.run(_run(SAFETY, ids))
    asyncio.run(_run(BARRIER, ids))
    other = asyncio.run(_seed())
    rules = parse_handler_config(synthetic_rules(), **APPROVALS)

    async def verify():
        engine = create_async_engine(config.database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session:
                with pytest.raises(HandlerConfigError, match="barrier unavailable"):
                    await save_action_plan_snapshot(
                        session,
                        user_id=UUID(other["user_id"]),
                        barrier_id=UUID(ids["barrier_id"]),
                        support_code=SupportCode.REMINDER_SETUP,
                        config=rules,
                    )
                await session.rollback()
            async with AsyncSession(engine) as session:
                plan = await save_action_plan_snapshot(
                    session,
                    user_id=UUID(ids["user_id"]),
                    barrier_id=UUID(ids["barrier_id"]),
                    support_code=SupportCode.REMINDER_SETUP,
                    config=rules,
                )
                plan_id = plan.id
                await session.commit()
            async with AsyncSession(engine) as session:
                snapshot = await restore_action_plan_snapshot(
                    session,
                    user_id=UUID(ids["user_id"]),
                    plan_id=plan_id,
                    historical_config=rules,
                )
                assert snapshot["parameters"] == {
                    "destination": "MEDICATION_SCHEDULE_SETUP",
                    "prescription_version_medication_id": ids["version_medication_id"],
                }
                assert set(snapshot) == {"schema_version", "rationale_code", "parameters"}
                with pytest.raises(HandlerConfigError, match="plan unavailable"):
                    await restore_action_plan_snapshot(
                        session,
                        user_id=UUID(other["user_id"]),
                        plan_id=plan_id,
                        historical_config=rules,
                    )
                newer = parse_handler_config(synthetic_rules("synthetic-v2"), **APPROVALS)
                with pytest.raises(HandlerConfigError, match="historical rule unavailable"):
                    await restore_action_plan_snapshot(
                        session,
                        user_id=UUID(ids["user_id"]),
                        plan_id=plan_id,
                        historical_config=newer,
                    )
        finally:
            await engine.dispose()

    asyncio.run(verify())
    assert asyncio.run(_run("SELECT count(*) FROM support_action_plan")) == 1
    asyncio.run(_run("UPDATE support_action_plan SET action_config_snapshot='{}'::jsonb"))

    async def reject_legacy():
        engine = create_async_engine(config.database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session:
                plan_id = await session.scalar(select(SupportActionPlan.id))
                with pytest.raises(HandlerConfigError, match="invalid historical snapshot"):
                    await restore_action_plan_snapshot(
                        session,
                        user_id=UUID(ids["user_id"]),
                        plan_id=plan_id,
                        historical_config=rules,
                    )
        finally:
            await engine.dispose()

    asyncio.run(reject_legacy())


def test_competing_active_plan_inserts_commit_only_one():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed())
    asyncio.run(_run(SAFETY, ids))
    asyncio.run(_run(BARRIER, ids))

    async def compete():
        async def create():
            try:
                await _run(PLAN, {**ids, "plan_id": str(uuid4())})
                return "created"
            except DBAPIError as error:
                assert "uq_action_plan_active_barrier" in str(error)
                return "conflict"

        return await asyncio.gather(create(), create())

    assert sorted(asyncio.run(compete())) == ["conflict", "created"]
    assert asyncio.run(_run("SELECT count(*) FROM support_action_plan WHERE status='ACTIVE'")) == 1


def test_safety_revisions_preserve_separate_checkin_histories():
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed())
    asyncio.run(_run(SAFETY, ids))
    with pytest.raises(DBAPIError, match="uq_safety_checkin_revision"):
        asyncio.run(_run(SAFETY, {**ids, "safety_id": str(uuid4())}))
    asyncio.run(_run(SAFETY.replace("1, 1,", "1, 2,"), {**ids, "safety_id": str(uuid4())}))
    asyncio.run(_run("UPDATE medication_checkin SET revision=2 WHERE id=:checkin_id", ids))
    asyncio.run(_run(SAFETY.replace("1, 1,", "2, 1,"), {**ids, "safety_id": str(uuid4())}))
    assert asyncio.run(_run("SELECT count(*) FROM safety_assessment")) == 3
