from typing import cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from app.dependencies.services import get_chat_engine, get_chat_service
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine
from app.services.chat_generator_engine import ChatGeneratorEngine


def test_get_chat_engine_builds_real_adapter() -> None:
    client = cast(AsyncOpenAI, AsyncMock())
    engine = get_chat_engine(client)
    assert isinstance(engine, ChatGeneratorEngine)


def test_get_chat_service_injects_exact_engine_and_repositories() -> None:
    prescription_repository = cast(PrescriptionRepository, object())
    chat_repository = cast(ChatRepository, object())
    engine = cast(ChatEngine, object())

    service = get_chat_service(prescription_repository, chat_repository, engine)

    assert service._prescription_repo is prescription_repository
    assert service._chat_repo is chat_repository
    assert service._engine is engine


def test_chat_service_requires_engine() -> None:
    with pytest.raises(TypeError):
        ChatService(  # type: ignore[call-arg]
            cast(PrescriptionRepository, object()),
            cast(ChatRepository, object()),
        )
