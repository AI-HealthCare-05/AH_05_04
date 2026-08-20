from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.core.errors import ApiError, ErrorDetail
from app.dtos.chat import SendChatMessageRequest
from app.models.chat import ChatGenerationStatus, ChatRole, ChatSessionStatus
from app.models.users import User
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat import ChatService
from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)

_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
_PRESCRIPTION_ID = UUID("00000000-0000-0000-0000-000000000002")
_SESSION_ID = UUID("00000000-0000-0000-0000-000000000003")
_USER_MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000004")
_ASSISTANT_MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000005")
_CREATED_AT = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
_COMPLETED_AT = datetime(2026, 8, 20, 9, 1, tzinfo=UTC)


class RecordingEngine:
    def __init__(self, *, error: Exception | None = None, events: list[str] | None = None) -> None:
        self.error = error
        self.events = events
        self.inputs: list[ChatReplyInput] = []

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        if self.events is not None:
            self.events.append("reply")
        self.inputs.append(chat_input)
        if self.error is not None:
            raise self.error
        return ChatReplyOutput(
            content="합성 답변",
            model_name="gpt-4o-mini-2024-07-18",
            prompt_version="chat-prompt-v1",
        )


def _user() -> User:
    return cast(User, SimpleNamespace(id=_USER_ID))


def _session(*, status: ChatSessionStatus = ChatSessionStatus.ACTIVE) -> SimpleNamespace:
    return SimpleNamespace(
        id=_SESSION_ID,
        prescription_id=_PRESCRIPTION_ID,
        session_status=status,
    )


def _user_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=_USER_MESSAGE_ID,
        role=ChatRole.USER,
        content="현재 질문",
        generation_status=ChatGenerationStatus.NOT_APPLICABLE,
        created_at=_CREATED_AT,
    )


def _assistant_message(
    *,
    generation_status: ChatGenerationStatus = ChatGenerationStatus.PENDING,
    content: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=_ASSISTANT_MESSAGE_ID,
        role=ChatRole.ASSISTANT,
        content=content,
        generation_status=generation_status,
        model_name="gpt-4o-mini-2024-07-18" if content is not None else None,
        prompt_version="chat-prompt-v1" if content is not None else None,
        created_at=_CREATED_AT,
        completed_at=_COMPLETED_AT if content is not None else None,
    )


def _medication(*, name: str = "합성약") -> SimpleNamespace:
    return SimpleNamespace(
        medication_name=name,
        dose_value=Decimal("0.500"),
        dose_unit="mg",
        frequency_per_day=2,
        timing_text="아침 식후",
        duration_days=7,
    )


def _repositories(*, events: list[str] | None = None) -> tuple[AsyncMock, AsyncMock]:
    prescription_repository = AsyncMock(spec=PrescriptionRepository)
    chat_repository = AsyncMock(spec=ChatRepository)
    chat_repository.get_session_owned_for_update.return_value = _session()
    chat_repository.next_seq.return_value = 1
    chat_repository.create_message.side_effect = [_user_message(), _assistant_message()]
    chat_repository.mark_generating.return_value = _assistant_message(generation_status=ChatGenerationStatus.GENERATING)
    chat_repository.mark_completed.return_value = _assistant_message(
        generation_status=ChatGenerationStatus.COMPLETED,
        content="합성 답변",
    )
    prescription_repository.get_medications.return_value = [_medication()]
    if events is not None:

        async def create_message(**kwargs: object) -> SimpleNamespace:
            role = kwargs["role"]
            events.append(f"create_{str(role).lower()}")
            if role == ChatRole.USER:
                return _user_message()
            return _assistant_message()

        async def mark_generating(message: SimpleNamespace) -> SimpleNamespace:
            events.append("mark_generating")
            return _assistant_message(generation_status=ChatGenerationStatus.GENERATING)

        async def mark_completed(message: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
            events.append("mark_completed")
            return _assistant_message(
                generation_status=ChatGenerationStatus.COMPLETED,
                content="합성 답변",
            )

        async def mark_failed(message: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
            events.append("mark_failed")
            return message

        async def update_last_message_at(session: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
            events.append("update_last_message_at")
            return session

        chat_repository.create_message.side_effect = create_message
        chat_repository.mark_generating.side_effect = mark_generating
        chat_repository.mark_completed.side_effect = mark_completed
        chat_repository.mark_failed.side_effect = mark_failed
        chat_repository.update_last_message_at.side_effect = update_last_message_at
    return prescription_repository, chat_repository


async def test_send_message_persists_one_cycle_and_preserves_medication_values() -> None:
    events: list[str] = []
    prescription_repository, chat_repository = _repositories(events=events)
    engine = RecordingEngine(events=events)
    service = ChatService(
        cast(PrescriptionRepository, prescription_repository),
        cast(ChatRepository, chat_repository),
        engine,
    )
    user = _user()

    result = await service.send_message(
        user=user,
        session_id=_SESSION_ID,
        request=SendChatMessageRequest(content="현재 질문"),
    )

    assert chat_repository.get_session_owned_for_update.await_args.kwargs == {
        "session_id": _SESSION_ID,
        "user_id": user.id,
    }
    assert engine.inputs[0].prescription_id == _PRESCRIPTION_ID
    assert engine.inputs[0].content == "현재 질문"
    assert engine.inputs[0].medications[0].dose_value == Decimal("0.500")
    assert engine.inputs[0].medications[0].duration_days == 7
    assert [call.kwargs["message_seq"] for call in chat_repository.create_message.await_args_list] == [1, 2]
    assert chat_repository.create_message.await_count == 2
    chat_repository.mark_completed.assert_awaited_once()
    assert events == [
        "create_user",
        "create_assistant",
        "mark_generating",
        "reply",
        "mark_completed",
        "update_last_message_at",
    ]
    assert result.content == "합성 답변"
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert result.prompt_version == "chat-prompt-v1"


async def test_send_message_rejects_session_not_owned_by_user_before_creating_messages() -> None:
    prescription_repository, chat_repository = _repositories()
    chat_repository.get_session_owned_for_update.return_value = None
    engine = RecordingEngine()
    service = ChatService(
        cast(PrescriptionRepository, prescription_repository),
        cast(ChatRepository, chat_repository),
        engine,
    )

    with pytest.raises(ApiError) as raised:
        await service.send_message(
            user=_user(),
            session_id=_SESSION_ID,
            request=SendChatMessageRequest(content="현재 질문"),
        )

    assert raised.value.status_code == 404
    assert raised.value.code == "CHAT_SESSION_NOT_FOUND"
    assert engine.inputs == []
    chat_repository.create_message.assert_not_awaited()


async def test_send_message_rejects_closed_session_before_creating_messages() -> None:
    prescription_repository, chat_repository = _repositories()
    chat_repository.get_session_owned_for_update.return_value = _session(status=ChatSessionStatus.CLOSED)
    engine = RecordingEngine()
    service = ChatService(
        cast(PrescriptionRepository, prescription_repository),
        cast(ChatRepository, chat_repository),
        engine,
    )

    with pytest.raises(ApiError) as raised:
        await service.send_message(
            user=_user(),
            session_id=_SESSION_ID,
            request=SendChatMessageRequest(content="현재 질문"),
        )

    assert raised.value.status_code == 409
    assert raised.value.code == "CONFLICT"
    assert engine.inputs == []
    chat_repository.create_message.assert_not_awaited()


async def test_send_message_preserves_repository_medication_order() -> None:
    prescription_repository, chat_repository = _repositories()
    prescription_repository.get_medications.return_value = [
        _medication(name="첫 번째 합성약"),
        _medication(name="두 번째 합성약"),
        _medication(name="세 번째 합성약"),
    ]
    engine = RecordingEngine()
    service = ChatService(
        cast(PrescriptionRepository, prescription_repository),
        cast(ChatRepository, chat_repository),
        engine,
    )

    await service.send_message(
        user=_user(),
        session_id=_SESSION_ID,
        request=SendChatMessageRequest(content="현재 질문"),
    )

    assert [item.medication_name for item in engine.inputs[0].medications] == [
        "첫 번째 합성약",
        "두 번째 합성약",
        "세 번째 합성약",
    ]


@pytest.mark.parametrize(
    ("engine_error", "status_code", "api_code", "field", "reason", "db_code", "db_message"),
    [
        (
            ChatTimeoutError("raw timeout"),
            504,
            "GATEWAY_TIMEOUT",
            "openai_api",
            "OPENAI_API_TIMEOUT",
            "OPENAI_API_TIMEOUT",
            "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
        ),
        (
            ChatServiceUnavailableError("raw unavailable"),
            503,
            "SERVICE_UNAVAILABLE",
            "openai_api",
            "OPENAI_API_ERROR",
            "OPENAI_API_ERROR",
            "OpenAI 서비스 호출에 실패했습니다.",
        ),
        (
            ChatGenerationFailedError("raw invalid response"),
            500,
            "AI_RESPONSE_FAILED",
            "assistant_message",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "챗봇 응답 생성 처리 중 오류가 발생했습니다.",
        ),
        (
            RuntimeError("raw synthetic medical payload"),
            500,
            "AI_RESPONSE_FAILED",
            "assistant_message",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "챗봇 응답 생성 처리 중 오류가 발생했습니다.",
        ),
    ],
)
async def test_send_message_persists_safe_failure_and_raises_detached_api_error(
    engine_error: Exception,
    status_code: int,
    api_code: str,
    field: str,
    reason: str,
    db_code: str,
    db_message: str,
) -> None:
    events: list[str] = []
    prescription_repository, chat_repository = _repositories(events=events)
    engine = RecordingEngine(error=engine_error, events=events)
    service = ChatService(
        cast(PrescriptionRepository, prescription_repository),
        cast(ChatRepository, chat_repository),
        engine,
    )

    with pytest.raises(ApiError) as raised:
        await service.send_message(
            user=_user(),
            session_id=_SESSION_ID,
            request=SendChatMessageRequest(content="현재 질문"),
        )

    assert raised.value.status_code == status_code
    assert raised.value.code == api_code
    assert (
        raised.value.message
        == {
            504: "외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            503: "현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            500: "AI 답변 생성에 실패했습니다.",
        }[status_code]
    )
    assert raised.value.details == [ErrorDetail(field=field, reason=reason)]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert chat_repository.create_message.await_count == 2
    assert [call.kwargs["message_seq"] for call in chat_repository.create_message.await_args_list] == [1, 2]
    chat_repository.mark_failed.assert_awaited_once()
    assert chat_repository.mark_failed.await_args.kwargs["error_code"] == db_code
    assert chat_repository.mark_failed.await_args.kwargs["error_message"] == db_message
    chat_repository.mark_completed.assert_not_awaited()
    assert events == [
        "create_user",
        "create_assistant",
        "mark_generating",
        "reply",
        "mark_failed",
    ]
