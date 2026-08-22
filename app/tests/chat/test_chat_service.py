from collections.abc import Generator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.dtos.chat import SendChatMessageRequest
from app.models.chat import ChatGenerationStatus, ChatRole, ChatSessionStatus
from app.services.chat import ChatService
from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> Generator[None]:
    yield


@pytest.fixture(autouse=True)
def isolate_database() -> Generator[None]:
    yield


class RecordingEngine:
    def __init__(self, *, result: ChatReplyOutput | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.inputs: list[ChatReplyInput] = []
        self.events: list[str] | None = None

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.inputs.append(chat_input)
        if self.events is not None:
            self.events.append("engine.reply")
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class RecordingPrescriptionRepository:
    def __init__(self, medications: Sequence[object], events: list[str]) -> None:
        self.medications = medications
        self.events = events

    async def get_medications(self, *, prescription_id: object) -> Sequence[object]:
        self.events.append("prescription.get_medications")
        return self.medications


class RecordingChatRepository:
    def __init__(self, session: object | None, events: list[str], *, commit_error: Exception | None = None) -> None:
        self.owned_session = session
        self.events = events
        self.commit_error = commit_error
        self.messages: list[SimpleNamespace] = []
        self.created_snapshots: list[tuple[int, object, object, object]] = []
        self.state_transitions: list[tuple[int, ChatGenerationStatus]] = []
        self.commits = 0

    async def get_session_owned_for_update(self, *, session_id: object, user_id: object) -> object | None:
        self.events.append("chat.lock_owned")
        return self.owned_session

    async def next_seq(self, *, session: object) -> int:
        self.events.append("chat.next_seq")
        return 7

    async def create_message(self, **kwargs: object) -> SimpleNamespace:
        role = kwargs["role"]
        session = cast(SimpleNamespace, kwargs["session"])
        self.events.append(f"chat.create.{role}")
        message = SimpleNamespace(
            id=uuid4(),
            session_id=session.id,
            message_seq=kwargs["message_seq"],
            role=role,
            content=kwargs["content"],
            generation_status=kwargs["generation_status"],
            model_name=None,
            prompt_version=None,
            error_code=None,
            error_message=None,
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
            completed_at=None,
        )
        self.messages.append(message)
        self.created_snapshots.append((message.message_seq, message.role, message.generation_status, message.content))
        return message

    async def mark_generating(self, message: SimpleNamespace) -> SimpleNamespace:
        self.events.append("chat.mark_generating")
        message.generation_status = ChatGenerationStatus.GENERATING
        self.state_transitions.append((message.message_seq, message.generation_status))
        return message

    async def mark_completed(self, message: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
        self.events.append("chat.mark_completed")
        message.content = kwargs["content"]
        message.model_name = kwargs["model_name"]
        message.prompt_version = kwargs["prompt_version"]
        message.completed_at = kwargs["completed_at"]
        message.generation_status = ChatGenerationStatus.COMPLETED
        return message

    async def update_last_message_at(self, session: SimpleNamespace, *, last_message_at: datetime) -> object:
        self.events.append("chat.update_last_message_at")
        session.last_message_at = last_message_at
        return session

    async def commit_failed_message_pair(self, message: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
        self.events.append("chat.commit_failed_message_pair")
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error
        message.generation_status = ChatGenerationStatus.FAILED
        message.error_code = kwargs["error_code"]
        message.error_message = kwargs["error_message"]
        message.completed_at = kwargs["completed_at"]
        return message


def _service_fixture(
    *,
    engine: RecordingEngine,
    status: ChatSessionStatus = ChatSessionStatus.ACTIVE,
    owned: bool = True,
    commit_error: Exception | None = None,
) -> tuple[ChatService, RecordingChatRepository, list[str], SimpleNamespace]:
    events: list[str] = []
    chat_session = SimpleNamespace(
        id=uuid4(),
        prescription_id=uuid4(),
        session_status=status,
        last_message_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    medications = [
        SimpleNamespace(
            medication_name="첫 번째 합성약",
            dose_value=Decimal("0.123"),
            dose_unit="mg",
            frequency_per_day=2,
            timing_text="식후",
            duration_days=9,
        ),
        SimpleNamespace(
            medication_name="두 번째 합성약",
            dose_value=None,
            dose_unit=None,
            frequency_per_day=None,
            timing_text=None,
            duration_days=None,
        ),
    ]
    chat_repo = RecordingChatRepository(chat_session if owned else None, events, commit_error=commit_error)
    prescription_repo = RecordingPrescriptionRepository(medications, events)
    engine.events = events
    return ChatService(prescription_repo, chat_repo, engine), chat_repo, events, chat_session  # type: ignore[arg-type]


async def test_send_message_locks_then_preserves_ordered_medication_fields_and_completes_pair() -> None:
    engine = RecordingEngine(
        result=ChatReplyOutput(content="안전한 합성 답변", model_name="model-id", prompt_version="prompt-v1")
    )
    service, chat_repo, events, chat_session = _service_fixture(engine=engine)
    user = SimpleNamespace(id=uuid4())

    result = await service.send_message(
        user=user,  # type: ignore[arg-type]
        session_id=chat_session.id,
        request=SendChatMessageRequest(content="합성 질문"),
    )

    assert events == [
        "chat.lock_owned",
        "prescription.get_medications",
        "chat.next_seq",
        f"chat.create.{ChatRole.USER}",
        f"chat.create.{ChatRole.ASSISTANT}",
        "chat.mark_generating",
        "engine.reply",
        "chat.mark_completed",
        "chat.update_last_message_at",
    ]
    assert chat_repo.created_snapshots == [
        (7, ChatRole.USER, ChatGenerationStatus.NOT_APPLICABLE, "합성 질문"),
        (8, ChatRole.ASSISTANT, ChatGenerationStatus.PENDING, None),
    ]
    assert chat_repo.state_transitions == [(8, ChatGenerationStatus.GENERATING)]
    assert chat_repo.messages[1].generation_status == ChatGenerationStatus.COMPLETED
    first_medication, second_medication = engine.inputs[0].medications
    assert (
        first_medication.medication_name,
        first_medication.dose_value,
        first_medication.dose_unit,
        first_medication.frequency_per_day,
        first_medication.timing_text,
        first_medication.duration_days,
    ) == ("첫 번째 합성약", Decimal("0.123"), "mg", 2, "식후", 9)
    assert isinstance(first_medication.dose_value, Decimal)
    assert (
        second_medication.medication_name,
        second_medication.dose_value,
        second_medication.dose_unit,
        second_medication.frequency_per_day,
        second_medication.timing_text,
        second_medication.duration_days,
    ) == ("두 번째 합성약", None, None, None, None, None)
    assert [item.medication_name for item in engine.inputs[0].medications] == ["첫 번째 합성약", "두 번째 합성약"]
    assert result.content == "안전한 합성 답변"
    assert result.model_name == "model-id"
    assert result.prompt_version == "prompt-v1"
    assert result.completed_at == chat_session.last_message_at


@pytest.mark.parametrize(
    ("owned", "status", "expected_code"),
    [
        (False, ChatSessionStatus.ACTIVE, "CHAT_SESSION_NOT_FOUND"),
        (True, ChatSessionStatus.CLOSED, "CONFLICT"),
    ],
)
async def test_send_message_rejects_ownership_or_status_before_engine(
    owned: bool, status: ChatSessionStatus, expected_code: str
) -> None:
    engine = RecordingEngine(result=ChatReplyOutput(content="unused", model_name="unused", prompt_version="unused"))
    service, _, events, chat_session = _service_fixture(engine=engine, owned=owned, status=status)

    with pytest.raises(ApiError) as captured:
        await service.send_message(
            user=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            session_id=chat_session.id,
            request=SendChatMessageRequest(content="합성 질문"),
        )

    assert captured.value.code == expected_code
    assert events == ["chat.lock_owned"]
    assert engine.inputs == []


@pytest.mark.parametrize(
    ("engine_error", "status", "api_code", "field", "reason", "db_code", "db_message"),
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
async def test_send_message_commits_fixed_safe_failure_then_raises_detached_api_error(
    engine_error: Exception,
    status: int,
    api_code: str,
    field: str,
    reason: str,
    db_code: str,
    db_message: str,
) -> None:
    service, chat_repo, events, chat_session = _service_fixture(engine=RecordingEngine(error=engine_error))

    with pytest.raises(ApiError) as captured:
        await service.send_message(
            user=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            session_id=chat_session.id,
            request=SendChatMessageRequest(content="합성 질문"),
        )

    error = captured.value
    assistant = chat_repo.messages[1]
    assert (error.status_code, error.code) == (status, api_code)
    assert [(detail.field, detail.reason) for detail in error.details] == [(field, reason)]
    assert error.__cause__ is None
    assert error.__context__ is None
    assert events[-2:] == ["engine.reply", "chat.commit_failed_message_pair"]
    assert chat_repo.commits == 1
    assert (assistant.error_code, assistant.error_message) == (db_code, db_message)
    assert assistant.completed_at is not None
    assert (assistant.content, assistant.model_name, assistant.prompt_version) == (None, None, None)


async def test_failed_pair_persistence_error_chain_excludes_provider_error() -> None:
    provider_error = ChatTimeoutError("raw provider payload")
    persistence_error = RuntimeError("synthetic commit failure")
    service, chat_repo, _, chat_session = _service_fixture(
        engine=RecordingEngine(error=provider_error), commit_error=persistence_error
    )

    with pytest.raises(RuntimeError) as captured:
        await service.send_message(
            user=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            session_id=chat_session.id,
            request=SendChatMessageRequest(content="합성 질문"),
        )

    assert captured.value is persistence_error
    chain: list[BaseException] = []
    current: BaseException | None = captured.value
    while current is not None:
        chain.append(current)
        current = current.__cause__ or current.__context__
    assert provider_error not in chain
