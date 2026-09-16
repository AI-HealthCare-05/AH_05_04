import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.config import Env
from app.dependencies.security import get_request_user
from app.dtos.feedback import FeedbackRequest
from app.evaluation.chat_history import evaluate_replay_dataset
from app.main import app, fastapi_app
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
from app.models.feedback import ChatMessageFeedback, GuideFeedback
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile
from app.models.users import User
from app.repositories.feedback_repository import FeedbackRepository
from app.services.feedback import FeedbackService
from app.tests.conftest import test_engine
from app.tests.services.test_guides import _create_confirmed_prescription, _create_user


async def seed(session: AsyncSession):
    user = await _create_user(session, email=f"feedback-{uuid4().hex[:12]}@example.com")
    prescription = await _create_confirmed_prescription(session, user=user)
    guide = Guide(
        prescription_id=prescription.id,
        prescription_version_id=prescription.active_version_id,
        profile_id=prescription.profile_id,
        generation_status=GuideGenerationStatus.COMPLETED,
        content="합성 안내",
        model_name="synthetic-model",
        prompt_version="synthetic-prompt",
    )
    chat = ChatSession(
        prescription_id=prescription.id,
        prescription_version_id=prescription.active_version_id,
        profile_id=prescription.profile_id,
    )
    session.add_all([guide, chat])
    await session.flush()
    message = ChatMessage(
        session_id=chat.id,
        message_seq=1,
        role=ChatRole.ASSISTANT,
        generation_status=ChatGenerationStatus.COMPLETED,
        content="합성 답변",
    )
    session.add(message)
    await session.flush()
    return user, guide, chat, message


@pytest.fixture
async def targets(db_session):
    user, guide, chat, message = await seed(db_session)
    await db_session.commit()
    identity = SimpleNamespace(id=user.id)
    fastapi_app.dependency_overrides[get_request_user] = lambda: identity
    try:
        yield user, guide, chat, message
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


def path(targets, chat):
    _, guide, session, message = targets
    return (
        f"/api/v1/chat-sessions/{session.id}/messages/{message.id}/feedback"
        if chat
        else f"/api/v1/guides/{guide.id}/feedback"
    )


@pytest.mark.parametrize("chat", [False, True])
async def test_submit_replay_update_delete_and_no_store(db_session, targets, chat, caplog):
    url = path(targets, chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {"rating": "NEGATIVE", "comment": "  SYNTHETIC_PRIVATE_SENTINEL  "}
        first = await client.post(url, json=body)
        assert first.status_code == 201, first.text
        assert first.headers["cache-control"] == "no-store"
        assert "SYNTHETIC_PRIVATE_SENTINEL" not in first.text
        replay = await client.post(url, json=body)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        changed = await client.post(url, json={"rating": "POSITIVE", "comment": "   "})
        assert changed.status_code == 200
        before, after = first.json()["data"], changed.json()["data"]
        assert before["id"] == after["id"]
        assert before["created_at"] == after["created_at"]
        assert after["rating"] == "POSITIVE"
        assert after["updated_at"] >= before["updated_at"]
        model = ChatMessageFeedback if chat else GuideFeedback
        row = await db_session.scalar(select(model))
        assert row.comment is None
        for _ in range(2):
            response = await client.delete(url)
            assert response.status_code == 204
            assert response.headers["cache-control"] == "no-store"
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in caplog.text


@pytest.mark.parametrize("chat", [False, True])
async def test_ownership_validation_and_auth(db_session, targets, chat, caplog):
    url = path(targets, chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for payload in (
            {},
            {"rating": 1},
            {"rating": "NEGATIVE", "comment": "X" * 1001},
            {"rating": "NEGATIVE", "comment": 42},
            {"rating": "NEGATIVE", "comment": "SENTINEL\x00"},
            {"rating": "NEGATIVE", "comment": "SENTINEL\ud800"},
            {"rating": "POSITIVE", "user_id": "SENTINEL"},
        ):
            response = await client.post(url, content=json.dumps(payload), headers={"Content-Type": "application/json"})
            assert response.status_code == 422
            assert "SENTINEL" not in response.text
        other = await _create_user(db_session, email=f"other-{uuid4().hex[:12]}@example.com")
        fastapi_app.dependency_overrides[get_request_user] = lambda: other
        foreign = await client.post(url, json={"rating": "NEGATIVE"})
        missing = await client.post(f"/api/v1/guides/{uuid4()}/feedback", json={"rating": "NEGATIVE"})
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["message"] == missing.json()["message"]
        assert (await client.delete(url)).status_code == 404
        fastapi_app.dependency_overrides.pop(get_request_user)
        assert (await client.post(url, json={"rating": "NEGATIVE"})).status_code == 401
    assert "SENTINEL" not in caplog.text


async def test_wrong_session_and_ineligible_targets(db_session, targets):
    _, guide, _, message = targets
    guide_url, chat_url = path(targets, False), path(targets, True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        mismatch = f"/api/v1/chat-sessions/{uuid4()}/messages/{message.id}/feedback"
        assert (await client.post(mismatch, json={"rating": "NEGATIVE"})).status_code == 404
        for state in (GuideGenerationStatus.PENDING, GuideGenerationStatus.GENERATING, GuideGenerationStatus.FAILED):
            guide.generation_status = state
            await db_session.flush()
            await db_session.commit()
            response = await client.post(guide_url, json={"rating": "NEGATIVE"})
            assert response.status_code == 409
        for role, state in (
            (ChatRole.USER, ChatGenerationStatus.NOT_APPLICABLE),
            (ChatRole.ASSISTANT, ChatGenerationStatus.PENDING),
            (ChatRole.ASSISTANT, ChatGenerationStatus.GENERATING),
            (ChatRole.ASSISTANT, ChatGenerationStatus.FAILED),
        ):
            message.role, message.generation_status = role, state
            await db_session.flush()
            await db_session.commit()
            assert (await client.post(chat_url, json={"rating": "NEGATIVE"})).status_code == 409


@pytest.mark.parametrize("environment", [Env.STAGING, Env.PRODUCTION])
async def test_nonlocal_collection_blocked(targets, monkeypatch, environment):
    monkeypatch.setattr(config, "ENV", environment)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for chat in (False, True):
            assert (await client.post(path(targets, chat), json={"rating": "POSITIVE"})).status_code == 404
            assert (await client.delete(path(targets, chat))).status_code == 404


async def test_expiry_uses_creation_not_update_and_target_cascade(db_session, targets):
    _, guide, _, message = targets
    now = datetime.now(UTC)
    expired = GuideFeedback(
        guide_id=guide.id, rating="NEGATIVE", comment="synthetic", created_at=now - timedelta(days=30), updated_at=now
    )
    current = ChatMessageFeedback(
        chat_message_id=message.id,
        rating="POSITIVE",
        created_at=now - timedelta(days=30) + timedelta(seconds=1),
        updated_at=now,
    )
    db_session.add_all([expired, current])
    await db_session.flush()
    assert await FeedbackRepository(db_session).purge_expired(now) == 1
    assert await db_session.scalar(select(func.count()).select_from(GuideFeedback)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ChatMessageFeedback)) == 1
    await db_session.execute(delete(ChatMessage).where(ChatMessage.id == message.id))
    assert await db_session.scalar(select(func.count()).select_from(ChatMessageFeedback)) == 0


async def test_expired_resubmission_is_new_and_deletion_does_not_require_ready_state(db_session, targets):
    user, guide, _, _ = targets
    old = GuideFeedback(
        guide_id=guide.id,
        rating="NEGATIVE",
        comment="synthetic expired",
        created_at=datetime.now(UTC) - timedelta(days=31),
        updated_at=datetime.now(UTC),
    )
    db_session.add(old)
    await db_session.flush()
    old_id = old.id
    service = FeedbackService(FeedbackRepository(db_session))
    result, created = await service.submit(
        user_id=user.id, target_id=guide.id, request=FeedbackRequest(rating="POSITIVE")
    )
    assert created
    assert result.id != old_id
    assert await db_session.scalar(select(func.count()).select_from(GuideFeedback)) == 1
    assert await db_session.scalar(select(GuideFeedback.id).where(GuideFeedback.id == old_id)) is None
    guide.generation_status = GuideGenerationStatus.FAILED
    await db_session.flush()
    await service.remove(user_id=user.id, target_id=guide.id)
    assert await db_session.scalar(select(func.count()).select_from(GuideFeedback)) == 0
    assert guide.generation_status == GuideGenerationStatus.FAILED


@pytest.mark.parametrize("chat", [False, True])
async def test_duplicate_first_requests_on_independent_connections(chat):
    async with AsyncSession(test_engine, expire_on_commit=False) as seed_session:
        user, guide, session, message = await seed(seed_session)
        await seed_session.commit()

    async def submit():
        async with AsyncSession(test_engine, expire_on_commit=False) as connection, connection.begin():
            result = await FeedbackService(FeedbackRepository(connection)).submit(
                user_id=user.id,
                target_id=message.id if chat else guide.id,
                session_id=session.id if chat else None,
                request=FeedbackRequest(rating="NEGATIVE"),
            )
            if result[1]:
                # Service has returned; its parent lock must survive until this outer commit.
                model, target_id = (ChatMessage, message.id) if chat else (Guide, guide.id)
                async with AsyncSession(test_engine) as probe:
                    await probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError) as error:
                        await probe.execute(select(model.id).where(model.id == target_id).with_for_update())
                    assert error.value.orig.sqlstate == "55P03"
            return result

    try:
        results = await asyncio.gather(submit(), submit(), return_exceptions=True)
        assert not any(isinstance(result, BaseException) for result in results), results
        assert sorted(created for _, created in results) == [False, True]
        assert results[0][0] == results[1][0]
    finally:
        # These connections commit outside the shared fixture's rollback boundary.
        # Remove the entire synthetic graph, including both targets and their parents.
        async with AsyncSession(test_engine) as cleanup:
            prescription = await cleanup.get(Prescription, guide.prescription_id)
            assert prescription is not None
            document_id, ocr_job_id = prescription.document_id, prescription.source_ocr_job_id
            for model, condition in (
                (ChatMessage, ChatMessage.id == message.id),
                (ChatSession, ChatSession.id == session.id),
                (Guide, Guide.id == guide.id),
                (Medication, Medication.prescription_id == prescription.id),
                (
                    PrescriptionVersionMedication,
                    PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id,
                ),
                (PrescriptionVersion, PrescriptionVersion.prescription_id == prescription.id),
                (Prescription, Prescription.id == prescription.id),
                (OcrJob, OcrJob.id == ocr_job_id),
                (MedicalDocument, MedicalDocument.id == document_id),
                (Profile, Profile.id == guide.profile_id),
                (User, User.id == user.id),
            ):
                await cleanup.execute(delete(model).where(condition))
            await cleanup.commit()
        async with AsyncSession(test_engine) as verification:
            for model, identity in (
                (User, user.id),
                (Prescription, guide.prescription_id),
                (Guide, guide.id),
                (ChatSession, session.id),
                (ChatMessage, message.id),
            ):
                assert await verification.get(model, identity) is None


async def test_failed_flush_rolls_back_feedback(db_session, targets, monkeypatch):
    user, guide, _, _ = targets
    real_flush = db_session.flush

    async def fail_after_flush(*args, **kwargs):
        await real_flush(*args, **kwargs)
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(db_session, "flush", fail_after_flush)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await FeedbackService(FeedbackRepository(db_session)).submit(
            user_id=user.id, target_id=guide.id, request=FeedbackRequest(rating="NEGATIVE")
        )
    assert await db_session.scalar(select(func.count()).select_from(GuideFeedback)) == 0


async def test_database_constraints(db_session, targets):
    _, guide, _, _ = targets
    for values in ({"guide_id": uuid4(), "rating": "POSITIVE"}, {"guide_id": guide.id, "rating": "INVALID"}):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(GuideFeedback(**values))
                await db_session.flush()


def test_openapi_contract():
    schema = fastapi_app.openapi()
    request = schema["components"]["schemas"]["FeedbackRequest"]
    assert request["required"] == ["rating"]
    assert request["additionalProperties"] is False
    assert request["properties"]["rating"]["enum"] == ["POSITIVE", "NEGATIVE"]
    for url in (
        "/api/v1/guides/{guide_id}/feedback",
        "/api/v1/chat-sessions/{session_id}/messages/{message_id}/feedback",
    ):
        assert set(schema["paths"][url]) == {"post", "delete"}
        assert {"200", "201"} <= schema["paths"][url]["post"]["responses"].keys()


async def test_synthetic_negative_feedback_links_to_versioned_review_case(db_session, targets):
    root = Path(__file__).parents[4]
    provenance = json.loads((root / "evals/generation/chat-feedback-gold-v1.provenance.json").read_text())
    dataset_path = root / "evals/generation" / provenance["dataset"]
    assert hashlib.sha256(dataset_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == provenance["dataset_sha256"]
    source = root / "evals/generation" / provenance["source_dataset"]
    assert hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == provenance["source_sha256"]
    dataset = json.loads(dataset_path.read_text())
    assert dataset["cases"][:-1] == json.loads(source.read_text())["cases"]
    user, _, session, message = targets
    result, created = await FeedbackService(FeedbackRepository(db_session)).submit(
        user_id=user.id,
        session_id=session.id,
        target_id=message.id,
        request=FeedbackRequest(**provenance["synthetic_feedback"]),
    )
    assert created
    negative = await db_session.scalar(
        select(ChatMessageFeedback).where(ChatMessageFeedback.id == result.id, ChatMessageFeedback.rating == "NEGATIVE")
    )
    assert negative is not None
    # Only this test's synthetic fixture is linked; operational feedback is never exported.
    assert provenance["review_status"] == "PENDING"
    assert provenance["provider_invocation"] is False
    report = evaluate_replay_dataset(dataset)
    case = next(case for case in report.cases if case.case_id == provenance["case_id"])
    assert case.history.passed
    regression = deepcopy(dataset)
    regression["cases"][-1]["replay_outputs"]["history"] = "어느 약인지 약명, 제품명을 다시 알려주세요."
    rejected = evaluate_replay_dataset(regression)
    assert not rejected.cases[-1].history.passed
    assert not rejected.cases[-1].quality_dimensions["redundant_clarification"].passed


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_response_failure_rolls_back_request(db_session, targets, monkeypatch, chat, method):
    from app.apis.v1 import feedback_routers
    from app.core.db import databases

    url = path(targets, chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        if method == "DELETE":
            assert (await client.post(url, json={"rating": "NEGATIVE"})).status_code == 201
    model = ChatMessageFeedback if chat else GuideFeedback
    target_id = targets[3].id if chat else targets[1].id
    target_column = model.chat_message_id if chat else model.guide_id
    # Exercise the real request dependency's commit/rollback, inside the test's outer transaction.
    original_override = fastapi_app.dependency_overrides.pop(databases.get_db_session)
    monkeypatch.setattr(
        databases,
        "AsyncSessionFactory",
        lambda: AsyncSession(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"),
    )

    def fail_response(*args, **kwargs):
        raise RuntimeError("synthetic response assembly failure")

    monkeypatch.setattr(feedback_routers, "JSONResponse" if method == "POST" else "Response", fail_response)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.request(method, url, json={"rating": "NEGATIVE"} if method == "POST" else None)
        assert response.status_code == 500
        count = await db_session.scalar(select(func.count()).select_from(model).where(target_column == target_id))
        assert count == (1 if method == "DELETE" else 0)
    finally:
        fastapi_app.dependency_overrides[databases.get_db_session] = original_override
