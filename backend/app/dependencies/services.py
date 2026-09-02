from typing import Annotated, TypedDict

from fastapi import Depends, Request
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.db.databases import get_db_session
from app.core.provider_observability import (
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderOperation,
)
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
from app.services.chat_ai.prompt import PROMPT_VERSION as CHAT_PROMPT_VERSION
from app.services.chat_generator_engine import ChatGeneratorEngine
from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.guide_ai import GuideGenerator
from app.services.guide_ai import OpenAIResponsesClient as GuideOpenAIResponsesClient
from app.services.guide_ai.prompt import PROMPT_VERSION as GUIDE_PROMPT_VERSION
from app.services.guides import GuideService
from app.services.medical_documents import MedicalDocumentService
from app.services.ocr import OcrService
from app.services.ocr_ai import (
    LlmPrescriptionStructurer,
    OcrStructurer,
    OpenAIOcrStructureClient,
    RuleBasedPrescriptionStructurer,
)
from app.services.ocr_ai.prompt import PROMPT_VERSION as OCR_STRUCTURE_PROMPT_VERSION
from app.services.ocr_engine import OcrEngine
from app.services.prescriptions import PrescriptionService
from app.services.users import UserManageService


def get_openai_client(request: Request) -> AsyncOpenAI:
    return request.app.state.openai_client


def get_provider_call_context(request: Request) -> ProviderCallContext:
    return request.state.provider_call_context


class _ProviderObservabilityKwargs(TypedDict):
    context: ProviderCallContext
    descriptor: ProviderCallDescriptor


def _provider_observability_kwargs(
    context: ProviderCallContext,
    *,
    provider: Provider,
    operation: ProviderOperation,
    prompt_version: str | None,
) -> _ProviderObservabilityKwargs:
    if context is None:
        raise ValueError("Provider call context is required")
    return {
        "context": context,
        "descriptor": ProviderCallDescriptor(
            provider=provider,
            operation=operation,
            prompt_version=prompt_version,
        ),
    }


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
    context: Annotated[
        ProviderCallContext,
        Depends(get_provider_call_context),
    ],
) -> OcrStructurer:
    if not config.OCR_STRUCTURE_LLM_ENABLED:
        # 기본값은 OFF입니다.
        # 명시적으로 활성화하지 않으면 OCR 원문을 OpenAI에 전달하지 않고
        # 기존 규칙 기반 구조화기를 사용합니다.
        return RuleBasedPrescriptionStructurer()

    # 활성화된 환경에서만 CLOVA 전체 token을
    # OpenAI Structured Outputs로 변환합니다.
    return LlmPrescriptionStructurer(
        provider=OpenAIOcrStructureClient(
            client,
            **_provider_observability_kwargs(
                context,
                provider=Provider.OPENAI,
                operation=ProviderOperation.OCR_STRUCTURING,
                prompt_version=OCR_STRUCTURE_PROMPT_VERSION,
            ),
        ),
        model=config.OCR_STRUCTURE_MODEL,
        timeout_seconds=config.OCR_STRUCTURE_TIMEOUT_SECONDS,
    )


def get_ocr_engine(
    structurer: Annotated[
        OcrStructurer,
        Depends(get_ocr_structurer),
    ],
    context: Annotated[
        ProviderCallContext,
        Depends(get_provider_call_context),
    ],
) -> OcrEngine:
    return ClovaOcrEngine(
        invoke_url=config.CLOVA_OCR_INVOKE_URL,
        secret_key=config.CLOVA_OCR_SECRET,
        storage_dir=config.STORAGE_DIR,
        timeout_seconds=config.CLOVA_OCR_TIMEOUT_SECONDS,
        structurer=structurer,
        **_provider_observability_kwargs(
            context,
            provider=Provider.CLOVA_OCR,
            operation=ProviderOperation.PRESCRIPTION_RECOGNITION,
            prompt_version=None,
        ),
    )


def get_prescription_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> PrescriptionRepository:
    return PrescriptionRepository(session)


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
    # PATCH가 lock 획득 이후 확정 여부를 다시 확인할 때 사용합니다.
    prescription_repository: Annotated[
        PrescriptionRepository,
        Depends(get_prescription_repository),
    ],
) -> OcrService:
    return OcrService(
        document_repository,
        ocr_repository,
        engine,
        prescription_repository,
    )


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
    context: Annotated[
        ProviderCallContext,
        Depends(get_provider_call_context),
    ],
) -> GuideGenerator:
    return GuideGenerator(
        provider=GuideOpenAIResponsesClient(
            client,
            **_provider_observability_kwargs(
                context,
                provider=Provider.OPENAI,
                operation=ProviderOperation.GUIDE_GENERATION,
                prompt_version=GUIDE_PROMPT_VERSION,
            ),
        ),
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
    context: Annotated[
        ProviderCallContext,
        Depends(get_provider_call_context),
    ],
) -> ChatEngine:
    return ChatGeneratorEngine(
        provider=ChatOpenAIResponsesClient(
            client,
            **_provider_observability_kwargs(
                context,
                provider=Provider.OPENAI,
                operation=ProviderOperation.CHAT_GENERATION,
                prompt_version=CHAT_PROMPT_VERSION,
            ),
        ),
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
    return ChatService(
        prescription_repository,
        chat_repository,
        engine,
        history_context_enabled=config.CHAT_HISTORY_CONTEXT_ENABLED,
    )


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
