import asyncio
from time import monotonic
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.dtos.chat import SendChatMessageRequest
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
from app.models.users import User
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine, ChatReplyInput, ChatReplyOutput, ChatTimeoutError
from app.tests.chat_integration.conftest import CommittedChatFixture
from app.tests.conftest import test_engine

REFERENCE_GENERATION_DELAY_SECONDS = 0.08


class CommitControlledEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.first_entered = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.release_first = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        del chat_input
        self.calls += 1
        call_number = self.calls
        if call_number == 1:
            self.first_entered.set()
            await self.release_first.wait()
        else:
            self.second_entered.set()
        return ChatReplyOutput(
            content=f"합성 답변 {call_number}",
            model_name="synthetic-model",
            prompt_version="chat-prompt-test-v1",
        )


class ParallelBarrierEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        del chat_input
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.calls == 2:
            self.both_entered.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return ChatReplyOutput(
            content="합성 병렬 답변",
            model_name="synthetic-model",
            prompt_version="chat-prompt-test-v1",
        )


class DelayedEngine:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        del chat_input
        self.calls += 1
        call_number = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
        finally:
            self.active -= 1
        return ChatReplyOutput(
            content=f"지연 합성 답변 {call_number}",
            model_name="synthetic-model",
            prompt_version="chat-prompt-test-v1",
        )


class FailingEngine:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        del chat_input
        self.calls += 1
        raise self.error


async def _send(
    *,
    db_session: AsyncSession,
    engine: ChatEngine,
    user: User,
    chat_session_id: UUID,
    content: str,
    commit: bool,
) -> None:
    service = ChatService(PrescriptionRepository(db_session), ChatRepository(db_session), engine)
    await service.send_message(
        user=user,
        session_id=chat_session_id,
        request=SendChatMessageRequest(content=content),
    )
    if commit:
        await db_session.commit()


async def _cancel_pending(*tasks: asyncio.Task[None]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _connection_ids(*sessions: AsyncSession) -> list[int]:
    # PostgreSQL backend PID를 이용해 요청별로 서로 다른 DB 연결인지 확인합니다.
    return [int(await session.scalar(text("SELECT pg_backend_pid()"))) for session in sessions]


async def _messages(chat_session_id: UUID) -> list[ChatMessage]:
    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as verification_db:
        return list(
            (
                await verification_db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == chat_session_id)
                    .order_by(ChatMessage.message_seq)
                )
            )
            .scalars()
            .all()
        )


def _assert_completed_pairs(messages: list[ChatMessage], *, pair_count: int) -> None:
    assert [message.message_seq for message in messages] == list(range(1, pair_count * 2 + 1))
    assert [message.role for message in messages] == [ChatRole.USER, ChatRole.ASSISTANT] * pair_count
    assert [message.generation_status for message in messages] == [
        ChatGenerationStatus.NOT_APPLICABLE,
        ChatGenerationStatus.COMPLETED,
    ] * pair_count


async def test_same_session_second_request_cannot_generate_until_first_commits(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    chat_session_id = committed_chat_fixture.session_ids[0]
    engine = CommitControlledEngine()
    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
    ):
        assert len(set(await _connection_ids(first_db, second_db))) == 2
        first_task = asyncio.create_task(
            _send(
                db_session=first_db,
                engine=engine,
                user=committed_chat_fixture.user,
                chat_session_id=chat_session_id,
                content="첫 번째 합성 질문",
                commit=True,
            )
        )
        second_task: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(engine.first_entered.wait(), timeout=1)
            second_task = asyncio.create_task(
                _send(
                    db_session=second_db,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=chat_session_id,
                    content="두 번째 합성 질문",
                    commit=True,
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(engine.second_entered.wait(), timeout=0.2)
            assert not second_task.done()
            engine.release_first.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=3)
        finally:
            engine.release_first.set()
            if second_task is None:
                await _cancel_pending(first_task)
            else:
                await _cancel_pending(first_task, second_task)

    messages = await _messages(chat_session_id)
    assert engine.calls == 2
    _assert_completed_pairs(messages, pair_count=2)
    assert [message.content for message in messages] == [
        "첫 번째 합성 질문",
        "합성 답변 1",
        "두 번째 합성 질문",
        "합성 답변 2",
    ]


async def test_three_same_session_requests_store_six_collision_free_messages_without_response_bound(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    chat_session_id = committed_chat_fixture.session_ids[0]
    engine = DelayedEngine(0.02)
    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as third_db,
    ):
        assert len(set(await _connection_ids(first_db, second_db, third_db))) == 3
        tasks = [
            asyncio.create_task(
                _send(
                    db_session=db_session,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=chat_session_id,
                    content=f"합성 질문 {index}",
                    commit=True,
                )
            )
            for index, db_session in enumerate((first_db, second_db, third_db), start=1)
        ]
        try:
            # This timeout is only a test-harness deadlock guard, not a response-time contract.
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        finally:
            await _cancel_pending(*tasks)

    messages = await _messages(chat_session_id)
    assert engine.calls == 3
    assert engine.max_active == 1
    _assert_completed_pairs(messages, pair_count=3)


async def test_different_sessions_for_same_prescription_enter_generation_in_parallel(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    first_chat_session_id, second_chat_session_id = committed_chat_fixture.session_ids
    engine = ParallelBarrierEngine()
    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
    ):
        assert len(set(await _connection_ids(first_db, second_db))) == 2
        tasks = (
            asyncio.create_task(
                _send(
                    db_session=first_db,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=first_chat_session_id,
                    content="첫 세션 합성 질문",
                    commit=True,
                )
            ),
            asyncio.create_task(
                _send(
                    db_session=second_db,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=second_chat_session_id,
                    content="둘째 세션 합성 질문",
                    commit=True,
                )
            ),
        )
        try:
            await asyncio.wait_for(engine.both_entered.wait(), timeout=1)
            assert engine.max_active == 2
            engine.release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
        finally:
            engine.release.set()
            await _cancel_pending(*tasks)

    assert engine.calls == 2
    for chat_session_id in committed_chat_fixture.session_ids:
        _assert_completed_pairs(await _messages(chat_session_id), pair_count=1)


async def test_two_same_session_requests_reflect_two_generation_delays_only_as_reference_behavior(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    chat_session_id = committed_chat_fixture.session_ids[0]
    engine = DelayedEngine(REFERENCE_GENERATION_DELAY_SECONDS)
    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
    ):
        started_at = monotonic()
        tasks = (
            asyncio.create_task(
                _send(
                    db_session=first_db,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=chat_session_id,
                    content="지연 합성 질문 1",
                    commit=True,
                )
            ),
            asyncio.create_task(
                _send(
                    db_session=second_db,
                    engine=engine,
                    user=committed_chat_fixture.user,
                    chat_session_id=chat_session_id,
                    content="지연 합성 질문 2",
                    commit=True,
                )
            ),
        )
        try:
            # This generous timeout is only a test-harness deadlock guard, not a response-time contract.
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            elapsed = monotonic() - started_at
        finally:
            await _cancel_pending(*tasks)

    assert engine.calls == 2
    assert engine.max_active == 1
    assert elapsed >= REFERENCE_GENERATION_DELAY_SECONDS * 2 * 0.9
    _assert_completed_pairs(await _messages(chat_session_id), pair_count=2)


async def test_provider_failure_pair_survives_request_and_later_request_rollbacks(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    chat_session_id = committed_chat_fixture.session_ids[0]
    provider_error = ChatTimeoutError("raw synthetic provider payload")
    failing_engine = FailingEngine(provider_error)

    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as failed_db:
        service = ChatService(PrescriptionRepository(failed_db), ChatRepository(failed_db), failing_engine)
        with pytest.raises(ApiError) as captured:
            await service.send_message(
                user=committed_chat_fixture.user,
                session_id=chat_session_id,
                request=SendChatMessageRequest(content="실패 저장용 합성 질문"),
            )
        assert captured.value.code == "GATEWAY_TIMEOUT"
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        await failed_db.rollback()

    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as later_db:
        await _send(
            db_session=later_db,
            engine=DelayedEngine(0),
            user=committed_chat_fixture.user,
            chat_session_id=chat_session_id,
            content="롤백될 후속 합성 질문",
            commit=False,
        )
        await later_db.rollback()

    messages = await _messages(chat_session_id)
    assert failing_engine.calls == 1
    assert len(messages) == 2
    assert [message.message_seq for message in messages] == [1, 2]
    assert [message.role for message in messages] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert [message.generation_status for message in messages] == [
        ChatGenerationStatus.NOT_APPLICABLE,
        ChatGenerationStatus.FAILED,
    ]
    assert messages[0].content == "실패 저장용 합성 질문"
    assert (messages[1].error_code, messages[1].error_message) == (
        "OPENAI_API_TIMEOUT",
        "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
    )
    assert messages[1].completed_at is not None
    assert (messages[1].content, messages[1].model_name, messages[1].prompt_version) == (None, None, None)
    assert await _session_still_exists(chat_session_id)


async def _session_still_exists(chat_session_id: UUID) -> bool:
    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as db_session:
        return await db_session.get(ChatSession, chat_session_id) is not None
