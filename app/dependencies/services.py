from typing import Annotated

from fastapi import Depends, Request
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.db.databases import get_db_session
from app.repositories.chat_repository import ChatRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine
from app.services.chat_ai import OpenAIResponsesClient as ChatOpenAIResponsesClient
from app.services.chat_generator_engine import ChatGeneratorEngine
from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.guide_ai import GuideGenerator
from app.services.guide_ai import OpenAIResponsesClient as GuideOpenAIResponsesClient
from app.services.guides import GuideService
from app.services.medical_documents import MedicalDocumentService
from app.services.ocr import OcrService
from app.services.ocr_ai import (
    LlmPrescriptionStructurer,
    OcrStructurer,
    OpenAIOcrStructureClient,
)
from app.services.ocr_engine import OcrEngine
from app.services.prescriptions import PrescriptionService
from app.services.users import UserManageService


def get_openai_client(request: Request) -> AsyncOpenAI:
    return request.app.state.openai_client


def get_user_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> UserRepository:
    return UserRepository(session)


def get_medical_document_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> MedicalDocumentRepository:
    return MedicalDocumentRepository(session)


def get_medical_document_service(
    repository: Annotated[
        MedicalDocumentRepository,
        Depends(get_medical_document_repository),
    ],
) -> MedicalDocumentService:
    return MedicalDocumentService(repository)


def get_ocr_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> OcrRepository:
    return OcrRepository(session)


def get_ocr_structurer(
    client: Annotated[
        AsyncOpenAI,
        Depends(get_openai_client),
    ],
) -> OcrStructurer:
    # CLOVA 전체 token을 OpenAI Structured Outputs로 변환합니다.
    return LlmPrescriptionStructurer(
        provider=OpenAIOcrStructureClient(client),
        model=config.OCR_STRUCTURE_MODEL,
        timeout_seconds=config.OCR_STRUCTURE_TIMEOUT_SECONDS,
    )


def get_ocr_engine(
    structurer: Annotated[
        OcrStructurer,
        Depends(get_ocr_structurer),
    ],
) -> OcrEngine:
    return ClovaOcrEngine(
        invoke_url=config.CLOVA_OCR_INVOKE_URL,
        secret_key=config.CLOVA_OCR_SECRET,
        storage_dir=config.STORAGE_DIR,
        timeout_seconds=config.CLOVA_OCR_TIMEOUT_SECONDS,
        # 기존 정규식 파서가 아니라 전체 token용 LLM 구조화기를 연결합니다.
        structurer=structurer,
    )


def get_ocr_service(
    document_repository: Annotated[
        MedicalDocumentRepository,
        Depends(get_medical_document_repository),
    ],
    ocr_repository: Annotated[
        OcrRepository,
        Depends(get_ocr_repository),
    ],
    engine: Annotated[
        OcrEngine,
        Depends(get_ocr_engine),
    ],
) -> OcrService:
    return OcrService(
        document_repository,
        ocr_repository,
        engine,
    )


def get_prescription_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> PrescriptionRepository:
    return PrescriptionRepository(session)


def get_prescription_service(
    document_repository: Annotated[
        MedicalDocumentRepository,
        Depends(get_medical_document_repository),
    ],
    ocr_repository: Annotated[
        OcrRepository,
        Depends(get_ocr_repository),
    ],
    prescription_repository: Annotated[
        PrescriptionRepository,
        Depends(get_prescription_repository),
    ],
) -> PrescriptionService:
    return PrescriptionService(document_repository, ocr_repository, prescription_repository)


def get_guide_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> GuideRepository:
    return GuideRepository(session)


def get_guide_generator(
    client: Annotated[
        AsyncOpenAI,
        Depends(get_openai_client),
    ],
) -> GuideGenerator:
    return GuideGenerator(
        provider=GuideOpenAIResponsesClient(client),
        model=config.OPENAI_MODEL,
        timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
    )


def get_guide_service(
    repository: Annotated[
        GuideRepository,
        Depends(get_guide_repository),
    ],
    generator: Annotated[
        GuideGenerator,
        Depends(get_guide_generator),
    ],
) -> GuideService:
    return GuideService(repository, generator)


def get_chat_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> ChatRepository:
    return ChatRepository(session)


def get_chat_engine(
    client: Annotated[
        AsyncOpenAI,
        Depends(get_openai_client),
    ],
) -> ChatEngine:
    return ChatGeneratorEngine(
        provider=ChatOpenAIResponsesClient(client),
        model=config.OPENAI_MODEL,
        timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
    )


def get_chat_service(
    prescription_repository: Annotated[
        PrescriptionRepository,
        Depends(get_prescription_repository),
    ],
    chat_repository: Annotated[
        ChatRepository,
        Depends(get_chat_repository),
    ],
    engine: Annotated[
        ChatEngine,
        Depends(get_chat_engine),
    ],
) -> ChatService:
    return ChatService(prescription_repository, chat_repository, engine)


def get_auth_service(
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
) -> AuthService:
    return AuthService(repository)


def get_user_manage_service(
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
    auth_service: Annotated[
        AuthService,
        Depends(get_auth_service),
    ],
) -> UserManageService:
    return UserManageService(
        repository=repository,
        auth_service=auth_service,
    )
