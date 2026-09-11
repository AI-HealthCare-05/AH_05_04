"""#294 엔드투엔드: ai_worker가 실제 라이브 경로(SqlAlchemyOcrResultStore)로 OCR 결과를
저장할 때 누락된 필수 필드를 placeholder row로 채우고, Backend의 기존 PATCH·처방 확정
경로가 그 placeholder를 정상적으로 검수·확정까지 이어갈 수 있는지 실제 PostgreSQL에서
확인합니다. tests/integration/rag/test_identification_concurrency.py와 동일한 패턴으로
격리된 schema에 전체 ORM 스키마를 만들어 사용합니다.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.adapters.sqlalchemy_ocr_result_store import SqlAlchemyOcrResultStore
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import OcrHandlerSuccess, OcrRecognizedField
from app.core import config
from app.core.db.databases import Base
from app.core.errors import ApiError
from app.dtos.prescriptions import UpdateExtractedFieldRequest
from app.models.async_jobs import AiJobType
from app.models.medical_documents import MedicalDocument
from app.models.ocr import ExtractedField, FieldType, OcrJob, OcrStatus
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.ocr import OcrService
from app.services.prescriptions import PrescriptionService

pytestmark = pytest.mark.asyncio

EXTENSION_SCHEMA = "test_extensions"
TEST_SCHEMA = "ocr_placeholder_e2e_test"
TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    # pg_trgm은 테이블이 없는 EXTENSION_SCHEMA에 두고 조회 경로에만 더한다.
    # 테이블 조회는 TEST_SCHEMA로 한정되어 격리가 유지된다.
    connect_args={"server_settings": {"search_path": f"{TEST_SCHEMA},{EXTENSION_SCHEMA}"}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)


async def _ensure_trigram_extension(connection, schema: str) -> None:
    """pg_trgm을 테이블이 없는 전용 schema에 둔다.

    public이나 테스트 schema에 두면 `create_all`의 존재 검사가 다른 schema의 동명 테이블을
    보고 생성을 건너뛴다. 확장은 DB당 하나뿐이라 이미 다른 schema에 있으면 옮긴다.
    """
    await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    await connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA {schema}"))
    current = await connection.scalar(
        text(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
        )
    )
    if current != schema:
        await connection.execute(text(f"ALTER EXTENSION pg_trgm SET SCHEMA {schema}"))


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
        await _ensure_trigram_extension(connection, EXTENSION_SCHEMA)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


async def _create_owner(session: AsyncSession) -> User:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"ocr-e2e-{suffix}@example.com",
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{int(suffix, 16) % 100000000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user


async def _create_processing_ocr_job(session: AsyncSession, *, user: User) -> tuple[MedicalDocument, OcrJob]:
    """accept_ocr_job()이 실제로 만들어 두는 상태(PROCESSING, ai_job_id 연결)를 재현합니다."""
    profile = (await session.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key=f"test/{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()

    ai_job = await AsyncJobRepository(session).create_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        prescription_version_id=None,
    )
    ocr_job = OcrJob(document_id=document.id, ai_job_id=ai_job.id, ocr_status=OcrStatus.PROCESSING)
    session.add(ocr_job)
    await session.flush()
    return document, ocr_job


def _build_message(*, job_id, domain_id) -> WorkerMessage:
    now = datetime.now(UTC)
    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(job_id),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(domain_id),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


async def test_worker_saved_placeholder_can_be_confirmed_through_existing_patch_and_prescription_confirm() -> None:
    """엔드투엔드: ai_worker가 실제로 쓰는 저장 경로(SqlAlchemyOcrResultStore)로 OCR 완료를
    기록 → 누락된 필수 필드가 placeholder row로 생김 → placeholder 상태로는 여전히
    처방 확정이 막힘(회귀 확인) → 기존 PATCH /extracted-fields/{field_id}로 confirmed_value
    입력 → 처방 확정까지 성공해야 한다."""
    async with session_factory() as session:
        user = await _create_owner(session)
        document, ocr_job = await _create_processing_ocr_job(session, user=user)
        message = _build_message(job_id=ocr_job.ai_job_id, domain_id=ocr_job.id)

        result_store = SqlAlchemyOcrResultStore(session, clock=lambda: datetime.now(UTC))
        await result_store.save(
            message=message,
            result=OcrHandlerSuccess(
                event_id=message.event_id,
                job_id=message.job_id,
                handler_type=JobType.OCR,
                domain_id=message.domain_id,
                fields=(
                    # PRESCRIBED_DATE는 아예 인식되지 않았다 — #294가 실제로 재현된 시나리오.
                    OcrRecognizedField(
                        medication_index=1,
                        field_type="MEDICATION_NAME",
                        raw_value="테스트정",
                        confidence_score=0.9,
                        normalized_value=None,
                        normalization_version=None,
                    ),
                ),
                engine_name="CLOVA_OCR",
                model_version=None,
                prompt_version=None,
            ),
        )
        await session.commit()

    async with session_factory() as session:
        result = await session.execute(select(ExtractedField).where(ExtractedField.ocr_job_id == ocr_job.id))
        fields_by_type = {(f.medication_index, f.field_type): f for f in result.scalars().all()}
        assert len(fields_by_type) == 5  # MEDICATION_NAME + 4개 placeholder

        prescribed_date_placeholder = fields_by_type[(0, FieldType.PRESCRIBED_DATE)]
        assert prescribed_date_placeholder.raw_value is None
        assert prescribed_date_placeholder.confirmation_status.value == "UNCONFIRMED"

        ocr_repository = OcrRepository(session)
        document_repository = MedicalDocumentRepository(session)
        prescription_repository = PrescriptionRepository(session)
        ocr_service = OcrService(
            document_repository=document_repository,
            ocr_repository=ocr_repository,
            prescription_repository=prescription_repository,
        )
        prescription_service = PrescriptionService(document_repository, ocr_repository, prescription_repository)

        # placeholder 상태 그대로 확정을 시도하면 기존 회귀 로직(_field_value)이 여전히
        # PRESCRIPTION_REQUIRED_FIELD_MISSING으로 막아야 한다.
        with pytest.raises(ApiError) as exc_info:
            await prescription_service.confirm_prescription(user=user, document_id=document.id)
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"

        # 기존 PATCH 경로로 placeholder를 포함한 필수 필드를 전부 확인한다.
        confirmations = {
            (0, FieldType.PRESCRIBED_DATE): "2026-09-01",
            (1, FieldType.MEDICATION_NAME): "테스트정",
            (1, FieldType.DOSE_VALUE): "1",
            (1, FieldType.FREQUENCY_PER_DAY): "3",
            (1, FieldType.DURATION_DAYS): "7",
        }
        for key, value in confirmations.items():
            await ocr_service.update_extracted_field(
                user=user,
                field_id=fields_by_type[key].id,
                request=UpdateExtractedFieldRequest(confirmed_value=value),
            )

        prescription = await prescription_service.confirm_prescription(user=user, document_id=document.id)
        await session.commit()

        assert prescription.prescribed_date.isoformat() == "2026-09-01"
        assert len(prescription.medications) == 1
        assert prescription.medications[0].medication_name == "테스트정"
