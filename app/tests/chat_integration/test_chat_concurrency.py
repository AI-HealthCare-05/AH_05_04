import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dtos.chat import SendChatMessageRequest
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
from app.models.users import User
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine, ChatReplyInput, ChatReplyOutput
from app.tests.chat_integration.conftest import CommittedChatFixture
from app.tests.conftest import test_engine


class ControlledEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.first_entered = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.release_first = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.calls += 1
        if self.calls == 1:
            self.first_entered.set()
            await self.release_first.wait()
        else:
            self.second_entered.set()
        return ChatReplyOutput(
            content=f"합성 답변 {self.calls}",
            model_name="synthetic-model",
            prompt_version="chat-prompt-v1",
        )


class BarrierEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.calls += 1
        if self.calls == 2:
            self.both_entered.set()
        await self.release.wait()
        return ChatReplyOutput(
            content="합성 병렬 답변",
            model_name="synthetic-model",
            prompt_version="chat-prompt-v1",
        )


async def send_and_commit(
    *,
    session: AsyncSession,
    engine: ChatEngine,
    user: User,
    session_id: UUID,
) -> None:
    service = ChatService(PrescriptionRepository(session), ChatRepository(session), engine)
    await service.send_message(
        user=user,
        session_id=session_id,
        request=SendChatMessageRequest(content="현재 질문"),
    )
    await session.commit()


async def cancel_pending_tasks(*tasks: asyncio.Task[None]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_same_session_requests_serialize_through_commit(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    user = committed_chat_fixture.user
    chat_session_id = committed_chat_fixture.session_ids[0]
    engine = ControlledEngine()

    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
    ):
        assert await first_db.get(ChatSession, chat_session_id) is not None
        assert await second_db.get(ChatSession, chat_session_id) is not None

        first_task = asyncio.create_task(
            send_and_commit(session=first_db, engine=engine, user=user, session_id=chat_session_id)
        )
        second_task: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(engine.first_entered.wait(), timeout=1)
            second_task = asyncio.create_task(
                send_and_commit(session=second_db, engine=engine, user=user, session_id=chat_session_id)
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(engine.second_entered.wait(), timeout=0.2)
            assert not second_task.done()

            engine.release_first.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        finally:
            engine.release_first.set()
            if second_task is None:
                await cancel_pending_tasks(first_task)
            else:
                await cancel_pending_tasks(first_task, second_task)

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as verification_db:
        messages = list(
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

    assert engine.calls == 2
    assert [message.message_seq for message in messages] == [1, 2, 3, 4]
    assert [message.role for message in messages] == [
        ChatRole.USER,
        ChatRole.ASSISTANT,
        ChatRole.USER,
        ChatRole.ASSISTANT,
    ]
    assert [message.generation_status for message in messages] == [
        ChatGenerationStatus.NOT_APPLICABLE,
        ChatGenerationStatus.COMPLETED,
        ChatGenerationStatus.NOT_APPLICABLE,
        ChatGenerationStatus.COMPLETED,
    ]
    assert [message.content for message in messages] == [
        "현재 질문",
        "합성 답변 1",
        "현재 질문",
        "합성 답변 2",
    ]


async def test_different_sessions_generate_independently(
    committed_chat_fixture: CommittedChatFixture,
) -> None:
    user = committed_chat_fixture.user
    first_chat_session_id, second_chat_session_id = committed_chat_fixture.session_ids
    engine = BarrierEngine()

    async with (
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as first_db,
        AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as second_db,
    ):
        assert await first_db.get(ChatSession, first_chat_session_id) is not None
        assert await second_db.get(ChatSession, second_chat_session_id) is not None

        first_task = asyncio.create_task(
            send_and_commit(session=first_db, engine=engine, user=user, session_id=first_chat_session_id)
        )
        second_task = asyncio.create_task(
            send_and_commit(session=second_db, engine=engine, user=user, session_id=second_chat_session_id)
        )
        try:
            await asyncio.wait_for(engine.both_entered.wait(), timeout=1)
            engine.release.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        finally:
            engine.release.set()
            await cancel_pending_tasks(first_task, second_task)

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as verification_db:
        results: dict[UUID, list[ChatMessage]] = {}
        for chat_session_id in committed_chat_fixture.session_ids:
            results[chat_session_id] = list(
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

    assert engine.calls == 2
    for chat_session_id in committed_chat_fixture.session_ids:
        messages = results[chat_session_id]
        assert [message.message_seq for message in messages] == [1, 2]
        assert [message.role for message in messages] == [ChatRole.USER, ChatRole.ASSISTANT]
        assert [message.generation_status for message in messages] == [
            ChatGenerationStatus.NOT_APPLICABLE,
            ChatGenerationStatus.COMPLETED,
        ]
        assert [message.content for message in messages] == ["현재 질문", "합성 병렬 답변"]
