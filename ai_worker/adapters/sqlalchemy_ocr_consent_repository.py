"""#465 스키마 기반 읽기 전용 adapter. 매 검사마다 독립 transaction을 사용한다."""

from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, and_, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.tasks.ocr.consent import ConsentRow, ConsentSnapshot

_OCR = table("ocr_job", column("id", String(36)), column("ai_job_id", String(36)), column("document_id", String(36)))
_DOCUMENT = table(
    "medical_document", column("id", String(36)), column("uploaded_by", String(36)), column("profile_id", String(36))
)
_PROFILE = table("profile", column("id", String(36)), column("user_id", String(36)), column("profile_type", String(20)))
_USER = table("user", column("id", String(36)), column("account_status", String(25)), column("is_active", Boolean))
_CONSENT = table(
    "user_consent",
    column("id", String(36)),
    column("user_id", String(36)),
    column("purpose", String(20)),
    column("status", String(20)),
    column("policy_version", String(100)),
    column("granted_at", DateTime(timezone=True)),
    column("withdrawn_at", DateTime(timezone=True)),
)


class SqlAlchemyOcrConsentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_snapshot(self, *, domain_id: UUID, job_id: UUID) -> ConsentSnapshot:
        statement = (
            select(
                _USER.c.account_status,
                _USER.c.is_active,
                _DOCUMENT.c.uploaded_by,
                _PROFILE.c.user_id,
                _PROFILE.c.profile_type,
                _CONSENT.c.id.label("consent_id"),
                _CONSENT.c.purpose,
                _CONSENT.c.status,
                _CONSENT.c.policy_version,
                _CONSENT.c.granted_at,
                _CONSENT.c.withdrawn_at,
            )
            .select_from(
                _OCR.join(_DOCUMENT, _OCR.c.document_id == _DOCUMENT.c.id)
                .join(_PROFILE, _DOCUMENT.c.profile_id == _PROFILE.c.id)
                .join(_USER, _DOCUMENT.c.uploaded_by == _USER.c.id)
                .outerjoin(_CONSENT, and_(_CONSENT.c.user_id == _USER.c.id, _CONSENT.c.purpose == "OCR"))
            )
            .where(_OCR.c.id == str(domain_id), _OCR.c.ai_job_id == str(job_id))
        )
        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(statement)).one_or_none()
        if row is None:
            return ConsentSnapshot(row=None, account_status="ACTIVE", resource_owner_matches=False)
        consent = None
        if row.consent_id is not None:
            consent = ConsentRow(
                purpose=row.purpose,
                status=row.status,
                policy_version=row.policy_version,
                timestamps_valid=row.granted_at is not None and row.withdrawn_at is None,
            )
        return ConsentSnapshot(
            row=consent,
            account_status=row.account_status if row.is_active else "INACTIVE",
            resource_owner_matches=row.uploaded_by == row.user_id and row.profile_type == "SELF",
        )
