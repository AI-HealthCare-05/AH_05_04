from dataclasses import dataclass
from typing import Annotated, TypedDict, cast

from fastapi import Depends, Request
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.closed_demo_retrieval import (
    ClosedDemoRetrievalDatabaseConfig,
    ClosedDemoRetrievalService,
    build_closed_demo_retrieval_service,
)
from app.core.config import Env
from app.core.db.databases import AccountWithdrawalCleanupSessionFactory, get_db_session
from app.core.provider_observability import (
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderOperation,
)
from app.repositories.account_deletion_request_repository import AccountDeletionRequestRepository
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.email_verification_repository import EmailVerificationRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.lifestyle_times_repository import LifestyleTimesRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.medication_report_repository import MedicationReportRepository
from app.repositories.medication_schedule_queries import MedicationScheduleQueries
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.rag_runtime_repository import RagRuntimeRepository
from app.repositories.refresh_session_repository import RefreshSessionRepository
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.repositories.user_consent_repository import UserConsentRepository
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine
from app.services.chat_ai import OpenAIResponsesClient as ChatOpenAIResponsesClient
from app.services.chat_ai.prompt import (
    CLOSED_DEMO_EVIDENCE_PROMPT_VERSION,
)
from app.services.chat_ai.prompt import (
    PROMPT_VERSION as CHAT_PROMPT_VERSION,
)
from app.services.chat_generator_engine import ChatGeneratorEngine
from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.email_delivery import EmailSender, NoopEmailSender, SmtpEmailSender, SmtpEmailSenderConfig
from app.services.guide_ai import GuideGenerator
from app.services.guide_ai import OpenAIResponsesClient as GuideOpenAIResponsesClient
from app.services.guide_ai.prompt import PROMPT_VERSION as GUIDE_PROMPT_VERSION
from app.services.guide_sync_runtime_execution import (
    GuideSyncRuntimeAuthorityProvider,
    GuideSyncRuntimeExecution,
)
from app.services.guide_sync_runtime_lifecycle import GuideSyncRuntimeLifecycleProducer
from app.services.guides import GuideService
from app.services.idempotency import SnapshotCipher, SyncMutationIdempotencyService, get_default_snapshot_cipher
from app.services.job_intake import JobIntakeService
from app.services.job_status import JobStatusService
from app.services.lifestyle_times import LifestyleTimesService
from app.services.medical_documents import MedicalDocumentService
from app.services.medication_candidates import MedicationCandidateService
from app.services.medication_checkin_api import MedicationCheckinApiService
from app.services.medication_checkins import MedicationCheckinService
from app.services.medication_identification import MedicationIdentificationService
from app.services.medication_occurrences import PrescriptionVersionMedicationInvalidationService
from app.services.medication_reports import MedicationReportService
from app.services.medication_schedule_api import MedicationScheduleApiService
from app.services.medication_schedule_mutations import MedicationScheduleMutationService
from app.services.notifications import NotificationService
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
from app.services.rag_preflight import RagPreflightService
from app.services.track_c_api import TrackCApiService
from app.services.track_c_demo_safety import InternalDemoSafetyPolicy
from app.services.track_c_flow import ContractFoundationSafetyPolicy, SafetyPolicy, TrackCFlowService
from app.services.track_c_revision_invalidation import TrackCCheckinRevisionInvalidation
from app.services.track_c_support import TrackCSupportService
from app.services.user_consents import ConsentGateService, OcrConsentService
from app.services.users import UserConsentService, UserManageService
from rag_runtime.closed_demo_chat_query_binding import (
    ApprovedClosedDemoChatQueryHmacKey,
    ClosedDemoChatQueryBindingDependencyError,
    ClosedDemoChatQueryFingerprintProducer,
    ClosedDemoChatQueryVerifier,
    build_closed_demo_chat_query_fingerprint_producer,
    build_closed_demo_chat_query_verifier,
)
from rag_runtime.guide_query_binding import (
    ApprovedGuideQueryHmacKey,
    GuideQueryFingerprintDependencyError,
    GuideQueryFingerprintProducer,
    ProductionQueryBindingVerifier,
    build_guide_query_fingerprint_producer,
    build_production_query_binding_verifier,
)
from rag_runtime.guide_runtime_execution import GuideRuntimeExecutorFactoryPort


def get_ocr_consent_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OcrConsentService:
    return OcrConsentService(
        UserConsentRepository(session),
        current_policy_version=config.OCR_CONSENT_POLICY_VERSION,
    )


def get_consent_gate_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConsentGateService:
    return ConsentGateService(UserConsentRepository(session))


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


def get_user_consent_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> UserConsentRepository:
    return UserConsentRepository(session)


def get_account_deletion_request_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> AccountDeletionRequestRepository:
    return AccountDeletionRequestRepository(
        session,
        cleanup_session_factory=AccountWithdrawalCleanupSessionFactory,
    )


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


def get_medication_schedule_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> MedicationScheduleRepository:
    return MedicationScheduleRepository(session)


def get_prescription_version_medication_invalidation_service(
    repository: Annotated[
        MedicationScheduleRepository,
        Depends(get_medication_schedule_repository),
    ],
) -> PrescriptionVersionMedicationInvalidationService:
    return PrescriptionVersionMedicationInvalidationService(
        repository,
        notification_cancellation=NotificationRepository(repository.session),
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
    # PATCH가 lock 획득 이후 확정 여부를 다시 확인할 때 사용합니다.
    prescription_repository: Annotated[
        PrescriptionRepository,
        Depends(get_prescription_repository),
    ],
    consent_service: Annotated[OcrConsentService, Depends(get_ocr_consent_service)],
) -> OcrService:
    # API 계층의 OCR 접수/조회/필드 수정은 Provider를 직접 호출하지 않습니다.
    # 실제 Worker OCR 실행은 ai_worker의 handler/provider 경로에서 처리합니다.
    # 이 서비스의 execute_ocr 경로와 get_ocr_engine 의존성 정리는 후속 작업으로 분리합니다.
    return OcrService(
        document_repository,
        ocr_repository,
        prescription_repository=prescription_repository,
        consent_service=consent_service,
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
    schedule_invalidation: Annotated[
        PrescriptionVersionMedicationInvalidationService,
        Depends(get_prescription_version_medication_invalidation_service),
    ],
    consent_service: Annotated[OcrConsentService, Depends(get_ocr_consent_service)],
) -> PrescriptionService:
    return PrescriptionService(
        document_repository,
        ocr_repository,
        prescription_repository,
        schedule_invalidation,
        consent_service=consent_service,
    )


def get_medication_candidate_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> MedicationCandidateRepository:
    return MedicationCandidateRepository(session)


def get_medication_identification_service(
    repository: Annotated[
        MedicationCandidateRepository,
        Depends(get_medication_candidate_repository),
    ],
) -> MedicationIdentificationService:
    return MedicationIdentificationService(repository)


def get_idempotency_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> IdempotencyRepository:
    return IdempotencyRepository(session)


def get_snapshot_cipher() -> SnapshotCipher:
    # 알고리즘·키 관리 방식은 여전히 Privacy·Security 리뷰 대상입니다(#311). 이 provider가
    # 하나로 모아둔 지점이라, 리뷰 결과가 나오면 이 함수만 바꾸면 됩니다.
    return get_default_snapshot_cipher()


def get_sync_mutation_idempotency_service(
    repository: Annotated[
        IdempotencyRepository,
        Depends(get_idempotency_repository),
    ],
    cipher: Annotated[
        SnapshotCipher,
        Depends(get_snapshot_cipher),
    ],
) -> SyncMutationIdempotencyService:
    return SyncMutationIdempotencyService(repository, cipher)


def get_lifestyle_times_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency: Annotated[
        SyncMutationIdempotencyService,
        Depends(get_sync_mutation_idempotency_service),
    ],
) -> LifestyleTimesService:
    return LifestyleTimesService(LifestyleTimesRepository(session), idempotency)


def get_medication_candidate_service(
    repository: Annotated[
        MedicationCandidateRepository,
        Depends(get_medication_candidate_repository),
    ],
    identification_service: Annotated[
        MedicationIdentificationService,
        Depends(get_medication_identification_service),
    ],
    idempotency_service: Annotated[
        SyncMutationIdempotencyService,
        Depends(get_sync_mutation_idempotency_service),
    ],
) -> MedicationCandidateService:
    return MedicationCandidateService(repository, identification_service, idempotency_service)


def get_medication_checkin_api_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_service: Annotated[
        SyncMutationIdempotencyService,
        Depends(get_sync_mutation_idempotency_service),
    ],
) -> MedicationCheckinApiService:
    checkins = MedicationCheckinService(
        MedicationCheckinRepository(session),
        revision_invalidation=TrackCCheckinRevisionInvalidation(TrackCStorageRepository(session)),
    )
    return MedicationCheckinApiService(MedicationScheduleRepository(session), checkins, idempotency_service)


def get_track_c_api_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_service: Annotated[
        SyncMutationIdempotencyService,
        Depends(get_sync_mutation_idempotency_service),
    ],
) -> TrackCApiService:
    repository = TrackCStorageRepository(session)
    policy: SafetyPolicy = (
        InternalDemoSafetyPolicy(config) if config.TRACK_C_SAFETY_DEMO_ENABLED else ContractFoundationSafetyPolicy()
    )
    flow = TrackCFlowService(repository, policy)
    return TrackCApiService(repository, flow, idempotency_service)


def get_track_c_support_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_service: Annotated[
        SyncMutationIdempotencyService,
        Depends(get_sync_mutation_idempotency_service),
    ],
) -> TrackCSupportService:
    return TrackCSupportService(TrackCStorageRepository(session), idempotency_service)


def get_guide_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> GuideRepository:
    return GuideRepository(session)


@dataclass(frozen=True, slots=True)
class GuideQueryHmacKeyDependency:
    """Composition-root carrier for the active Guide query HMAC key only."""

    key_version: str
    _key: ApprovedGuideQueryHmacKey | None

    def active_key_version(self) -> str:
        return self.key_version

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None:
        if key_version != self.key_version:
            return None
        if self._key is None:
            raise GuideQueryFingerprintDependencyError()
        return self._key


def get_guide_query_hmac_key_dependency() -> GuideQueryHmacKeyDependency:
    """Translate Backend Config to the B2 typed key authority without a fallback."""

    secret = config.GUIDE_QUERY_HMAC_KEY
    key: ApprovedGuideQueryHmacKey | None = None
    if secret is not None:
        try:
            material = secret.get_secret_value()
            if material.strip():
                key = ApprovedGuideQueryHmacKey(material.encode())
        except Exception:
            key = None
    return GuideQueryHmacKeyDependency(config.GUIDE_QUERY_HMAC_KEY_VERSION, key)


def get_guide_query_fingerprint_producer(
    key_dependency: Annotated[
        GuideQueryHmacKeyDependency,
        Depends(get_guide_query_hmac_key_dependency),
    ],
) -> GuideQueryFingerprintProducer:
    return build_guide_query_fingerprint_producer(key_dependency)


def get_guide_query_binding_verifier(
    key_dependency: Annotated[
        GuideQueryHmacKeyDependency,
        Depends(get_guide_query_hmac_key_dependency),
    ],
) -> ProductionQueryBindingVerifier:
    return build_production_query_binding_verifier(key_dependency)


@dataclass(frozen=True, slots=True)
class ClosedDemoChatQueryHmacKeyDependency:
    """Composition-root carrier for the CLOSED_DEMO Chat HMAC authority only."""

    key_version: str
    _key: ApprovedClosedDemoChatQueryHmacKey | None

    def active_key_version(self) -> str:
        return self.key_version

    def key_for_version(self, key_version: str) -> ApprovedClosedDemoChatQueryHmacKey | None:
        if key_version != self.key_version:
            return None
        if self._key is None:
            raise ClosedDemoChatQueryBindingDependencyError()
        return self._key


def get_closed_demo_chat_query_hmac_key_dependency() -> ClosedDemoChatQueryHmacKeyDependency:
    """Translate Config into the independent CLOSED_DEMO Chat key authority."""

    secret = config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY
    key: ApprovedClosedDemoChatQueryHmacKey | None = None
    if secret is not None:
        try:
            material = secret.get_secret_value()
            if material.strip():
                key = ApprovedClosedDemoChatQueryHmacKey(material.encode())
        except Exception:
            key = None
    return ClosedDemoChatQueryHmacKeyDependency(config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY_VERSION, key)


def get_closed_demo_chat_query_fingerprint_producer(
    key_dependency: Annotated[
        ClosedDemoChatQueryHmacKeyDependency,
        Depends(get_closed_demo_chat_query_hmac_key_dependency),
    ],
) -> ClosedDemoChatQueryFingerprintProducer:
    return build_closed_demo_chat_query_fingerprint_producer(key_dependency)


def get_closed_demo_chat_query_binding_verifier(
    key_dependency: Annotated[
        ClosedDemoChatQueryHmacKeyDependency,
        Depends(get_closed_demo_chat_query_hmac_key_dependency),
    ],
) -> ClosedDemoChatQueryVerifier:
    return build_closed_demo_chat_query_verifier(key_dependency)


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


def get_guide_sync_runtime_execution(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    repository: Annotated[GuideRepository, Depends(get_guide_repository)],
    identification_service: Annotated[
        MedicationIdentificationService,
        Depends(get_medication_identification_service),
    ],
) -> GuideSyncRuntimeExecution | None:
    factory = getattr(request.app.state, "guide_runtime_executor_factory", None)
    authority_provider = getattr(request.app.state, "guide_sync_runtime_authority_provider", None)
    if factory is None and authority_provider is None:
        return None
    if factory is None or authority_provider is None:
        raise RuntimeError("Guide Sync runtime dependencies are incomplete")

    runtime_repository = RagRuntimeRepository(session)
    return GuideSyncRuntimeExecution(
        repository=repository,
        lifecycle=GuideSyncRuntimeLifecycleProducer(
            job_repository=AsyncJobRepository(session),
            runtime_repository=runtime_repository,
            preflight_service=RagPreflightService(identification_service),
        ),
        executor_factory=cast(GuideRuntimeExecutorFactoryPort, factory),
        authority_provider=cast(GuideSyncRuntimeAuthorityProvider, authority_provider),
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
    consent_gate: Annotated[
        ConsentGateService,
        Depends(get_consent_gate_service),
    ],
    runtime_execution: Annotated[
        GuideSyncRuntimeExecution | None,
        Depends(get_guide_sync_runtime_execution),
    ],
) -> GuideService:
    return GuideService(repository, generator, consent_gate, runtime_execution)


def get_chat_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> ChatRepository:
    return ChatRepository(session)


def build_configured_closed_demo_retrieval_service(client: AsyncOpenAI) -> ClosedDemoRetrievalService:
    """Build the one lifespan-owned CLOSED_DEMO retrieval composition."""
    password = config.SOURCE591_CONSUMER_PASSWORD
    query_key_dependency = get_closed_demo_chat_query_hmac_key_dependency()
    return build_closed_demo_retrieval_service(
        database_config=ClosedDemoRetrievalDatabaseConfig(
            host=config.SOURCE591_STAGING_DB_HOST,
            port=config.SOURCE591_STAGING_DB_PORT,
            database="source591_staging",
            username="source591_consumer",
            password=password.get_secret_value() if password is not None else "",
        ),
        openai_client=client,
        fingerprint_producer=get_closed_demo_chat_query_fingerprint_producer(query_key_dependency),
        binding_verifier=get_closed_demo_chat_query_binding_verifier(query_key_dependency),
    )


def get_closed_demo_retrieval_service(request: Request) -> ClosedDemoRetrievalService | None:
    """Return the lifespan-owned CLOSED_DEMO retriever without opening a new pool."""
    if not config.CHAT_CLOSED_DEMO_RAG_ENABLED:
        return None
    retriever = getattr(request.app.state, "closed_demo_retrieval_service", None)
    if retriever is None:
        raise RuntimeError("CLOSED_DEMO retrieval service was not initialized at startup")
    return retriever


def get_chat_engine(
    client: Annotated[
        AsyncOpenAI,
        Depends(get_openai_client),
    ],
    context: Annotated[
        ProviderCallContext,
        Depends(get_provider_call_context),
    ],
    closed_demo_retriever: Annotated[
        ClosedDemoRetrievalService | None,
        Depends(get_closed_demo_retrieval_service),
    ] = None,
) -> ChatEngine:
    prompt_version = CLOSED_DEMO_EVIDENCE_PROMPT_VERSION if closed_demo_retriever is not None else CHAT_PROMPT_VERSION
    provider = ChatOpenAIResponsesClient(
        client,
        **_provider_observability_kwargs(
            context,
            provider=Provider.OPENAI,
            operation=ProviderOperation.CHAT_GENERATION,
            prompt_version=prompt_version,
        ),
    )
    if closed_demo_retriever is None:
        return ChatGeneratorEngine(
            provider=provider,
            model=config.OPENAI_MODEL,
            timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
        )
    return ChatGeneratorEngine(
        provider=provider,
        model=config.OPENAI_MODEL,
        timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
        closed_demo_retriever=closed_demo_retriever,
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
    consent_gate: Annotated[
        ConsentGateService,
        Depends(get_consent_gate_service),
    ],
) -> ChatService:
    return ChatService(
        prescription_repository,
        chat_repository,
        engine,
        consent_gate,
        history_context_enabled=config.CHAT_HISTORY_CONTEXT_ENABLED,
    )


def get_password_reset_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> PasswordResetRepository:
    return PasswordResetRepository(session)


def get_email_verification_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> EmailVerificationRepository:
    return EmailVerificationRepository(session)


def _build_smtp_email_sender() -> SmtpEmailSender:
    return SmtpEmailSender(
        SmtpEmailSenderConfig(
            host=config.SMTP_HOST,
            port=config.SMTP_PORT,
            username=config.SMTP_USERNAME,
            password=config.SMTP_PASSWORD,
            from_email=config.SMTP_FROM_EMAIL,
            use_tls=config.SMTP_USE_TLS,
            timeout_seconds=config.SMTP_TIMEOUT_SECONDS,
        )
    )


def get_email_sender() -> EmailSender:
    if config.EMAIL_PROVIDER == "smtp":
        return _build_smtp_email_sender()
    if config.ENV is not Env.LOCAL:
        raise RuntimeError("EMAIL_PROVIDER=noop is allowed only in local environment")
    return NoopEmailSender()


def get_refresh_session_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> RefreshSessionRepository:
    return RefreshSessionRepository(session)


def get_auth_service(
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
    password_reset_repository: Annotated[
        PasswordResetRepository,
        Depends(get_password_reset_repository),
    ],
    refresh_session_repository: Annotated[
        RefreshSessionRepository,
        Depends(get_refresh_session_repository),
    ],
    email_verification_repository: Annotated[
        EmailVerificationRepository,
        Depends(get_email_verification_repository),
    ],
    email_sender: Annotated[
        EmailSender,
        Depends(get_email_sender),
    ],
    user_consent_repository: Annotated[
        UserConsentRepository,
        Depends(get_user_consent_repository),
    ],
    account_deletion_request_repository: Annotated[
        AccountDeletionRequestRepository,
        Depends(get_account_deletion_request_repository),
    ],
) -> AuthService:
    return AuthService(
        repository,
        password_reset_repository,
        refresh_session_repository,
        email_verification_repository,
        email_sender,
        user_consent_repository,
        account_deletion_request_repository,
    )


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


def get_user_consent_service(
    repository: Annotated[
        UserConsentRepository,
        Depends(get_user_consent_repository),
    ],
) -> UserConsentService:
    return UserConsentService(repository)


def get_async_job_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> AsyncJobRepository:
    return AsyncJobRepository(session)


def get_job_intake_service(
    repository: Annotated[
        AsyncJobRepository,
        Depends(get_async_job_repository),
    ],
) -> JobIntakeService:
    return JobIntakeService(repository)


def get_job_status_service(
    job_repository: Annotated[
        AsyncJobRepository,
        Depends(get_async_job_repository),
    ],
    ocr_repository: Annotated[
        OcrRepository,
        Depends(get_ocr_repository),
    ],
    guide_repository: Annotated[
        GuideRepository,
        Depends(get_guide_repository),
    ],
    chat_repository: Annotated[
        ChatRepository,
        Depends(get_chat_repository),
    ],
) -> JobStatusService:
    return JobStatusService(
        job_repository=job_repository,
        ocr_repository=ocr_repository,
        guide_repository=guide_repository,
        chat_repository=chat_repository,
    )


def get_notification_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency: Annotated[SyncMutationIdempotencyService, Depends(get_sync_mutation_idempotency_service)],
) -> NotificationService:
    return NotificationService(NotificationRepository(session), idempotency)


def get_medication_schedule_api_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_service: Annotated[SyncMutationIdempotencyService, Depends(get_sync_mutation_idempotency_service)],
) -> MedicationScheduleApiService:
    return MedicationScheduleApiService(
        MedicationScheduleQueries(session),
        MedicationScheduleMutationService(
            MedicationScheduleRepository(session),
            notification_cancellation=NotificationRepository(session),
        ),
        idempotency_service,
    )


def get_medication_report_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MedicationReportService:
    return MedicationReportService(MedicationReportRepository(session))
