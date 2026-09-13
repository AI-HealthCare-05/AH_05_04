from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.async_jobs import AiJob, AiJobAttempt, AiJobAttemptStatus, AiJobStatus, OutboxEvent, OutboxEventStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import (
    Prescription,
    PrescriptionVersion,
    PrescriptionVersionMedication,
)
from app.models.rag_candidate import MedicationCandidateSearch, MedicationCandidateSearchStatus
from app.repositories.prescription_integrity import require_verified_version, unavailable_version, verify_loaded_version
from app.repositories.profile_ownership import owned_by_self
from provider_contracts.prescription_integrity import MEDICATION_CONTENT_FIELDS, prescription_fingerprint


class PrescriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_document(self, *, document: MedicalDocument) -> Prescription | None:
        result = await self.session.execute(select(Prescription).where(Prescription.document_id == document.id))
        return result.scalar_one_or_none()

    async def get_owned(self, *, prescription_id: UUID, user_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .options(
                selectinload(Prescription.document),
                selectinload(Prescription.active_version).selectinload(PrescriptionVersion.medications),
            )
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        prescription = result.scalar_one_or_none()
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def get_latest_owned(self, *, user_id: UUID) -> Prescription | None:
        """재접속 복구 지원: Frontend가 어떤 prescription_id도 들고 있지 않을 때
        (로그아웃·재로그인 등) 이 사용자의 SELF profile 소유 처방 중 가장 최근 확정 건을
        돌려줍니다. `idx_prescription_profile_created`(profile_id, created_at, id)를 사용합니다."""
        result = await self.session.execute(
            select(Prescription)
            .options(
                selectinload(Prescription.document),
                selectinload(Prescription.active_version).selectinload(PrescriptionVersion.medications),
            )
            .where(owned_by_self(Prescription.profile_id, user_id))
            .order_by(Prescription.created_at.desc(), Prescription.id.desc())
            .limit(1)
        )
        prescription = result.scalar_one_or_none()
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def create_with_medications(
        self,
        *,
        document: MedicalDocument,
        source_ocr_job: OcrJob,
        prescribed_date: date,
        confirmed_at: datetime,
        medications: list[dict],
    ) -> Prescription:
        prescription_fingerprint(prescribed_date, medications)
        async with self.session.begin_nested():
            return await self._create_with_medications(
                document=document,
                source_ocr_job=source_ocr_job,
                prescribed_date=prescribed_date,
                confirmed_at=confirmed_at,
                medications=medications,
            )

    async def create_version(
        self,
        *,
        prescription: Prescription,
        prescribed_date: date,
        confirmed_at: datetime,
        medications: list[dict],
    ) -> PrescriptionVersion:
        prescription_fingerprint(prescribed_date, medications)
        async with self.session.begin_nested():
            # Service의 expected revision 검사도 이 부모 잠금 안에서 수행해야 합니다.
            locked = await self.session.scalar(
                select(Prescription.id).where(Prescription.id == prescription.id).with_for_update()
            )
            if locked is None:
                raise ValueError("Prescription does not exist")
            return await self._create_version(
                prescription=prescription,
                prescribed_date=prescribed_date,
                confirmed_at=confirmed_at,
                medications=medications,
            )

    async def _create_with_medications(
        self,
        *,
        document: MedicalDocument,
        source_ocr_job: OcrJob,
        prescribed_date: date,
        confirmed_at: datetime,
        medications: list[dict],
    ) -> Prescription:
        version_id = uuid4()
        prescription = Prescription(
            active_version_id=version_id,
            document_id=document.id,
            source_ocr_job_id=source_ocr_job.id,
            profile_id=document.profile_id,
            prescribed_date=prescribed_date,
            confirmed_at=confirmed_at,
        )
        self.session.add(prescription)
        await self.session.flush()

        fingerprint = prescription_fingerprint(prescribed_date, medications)
        version = PrescriptionVersion(
            medication_count=fingerprint.medication_count,
            content_hash=fingerprint.content_hash,
            id=version_id,
            prescription_id=prescription.id,
            version_number=1,
            prescribed_date=prescribed_date,
            confirmed_at=confirmed_at,
        )
        self.session.add(version)
        await self.session.flush()

        for medication in medications:
            self.session.add(
                PrescriptionVersionMedication(
                    prescription_version_id=version.id,
                    medication_count=fingerprint.medication_count,
                    **medication,
                )
            )
        await self.session.flush()
        await self._verify_medication_membership(version.id, prescribed_date, medications)
        return prescription

    async def _verify_medication_membership(
        self, version_id: UUID, prescribed_date: date, medications: list[dict]
    ) -> None:
        rows = (
            (
                await self.session.execute(
                    select(*(getattr(PrescriptionVersionMedication, key) for key in MEDICATION_CONTENT_FIELDS)).where(
                        PrescriptionVersionMedication.prescription_version_id == version_id
                    )
                )
            )
            .mappings()
            .all()
        )
        if prescription_fingerprint(prescribed_date, [dict(row) for row in rows]) != prescription_fingerprint(
            prescribed_date, medications
        ):
            raise ValueError("Persisted prescription medication content differs from requested content")

    async def get_version_medications(self, *, prescription_version_id: UUID) -> list[PrescriptionVersionMedication]:
        rows = (
            await self.session.execute(
                select(PrescriptionVersion, PrescriptionVersionMedication)
                .join(
                    PrescriptionVersionMedication,
                    PrescriptionVersionMedication.prescription_version_id == PrescriptionVersion.id,
                )
                .where(PrescriptionVersion.id == prescription_version_id)
                .order_by(PrescriptionVersionMedication.display_order)
                .execution_options(populate_existing=True)
            )
        ).all()
        if not rows:
            raise unavailable_version()
        medications = [row[1] for row in rows]
        verify_loaded_version(rows[0][0], medications)
        return medications

    async def get_version(self, *, prescription_version_id: UUID) -> PrescriptionVersion | None:
        return await self.session.get(PrescriptionVersion, prescription_version_id)

    async def get_owned_for_version_update(self, *, prescription_id: UUID, user_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        prescription = result.scalar_one_or_none()
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def _create_version(
        self,
        *,
        prescription: Prescription,
        prescribed_date: date,
        confirmed_at: datetime,
        medications: list[dict],
    ) -> PrescriptionVersion:
        latest_revision = await self.session.scalar(
            select(PrescriptionVersion.version_number)
            .where(PrescriptionVersion.prescription_id == prescription.id)
            .order_by(PrescriptionVersion.version_number.desc())
            .limit(1)
        )
        fingerprint = prescription_fingerprint(prescribed_date, medications)
        version = PrescriptionVersion(
            medication_count=fingerprint.medication_count,
            content_hash=fingerprint.content_hash,
            prescription_id=prescription.id,
            version_number=(latest_revision or 0) + 1,
            prescribed_date=prescribed_date,
            confirmed_at=confirmed_at,
        )
        self.session.add(version)
        await self.session.flush()
        for medication in medications:
            self.session.add(
                PrescriptionVersionMedication(
                    prescription_version_id=version.id,
                    medication_count=fingerprint.medication_count,
                    **medication,
                )
            )
        await self.session.flush()
        await self._verify_medication_membership(version.id, prescribed_date, medications)
        prescription.active_version_id = version.id
        await self.session.flush()
        return version

    async def invalidate_version_domain_dependencies(
        self,
        *,
        prescription_version_id: UUID,
        invalidated_at: datetime,
    ) -> list[UUID]:
        """이전 Version의 실행 중 Job과 재사용 가능한 Candidate를 무효화합니다.

        호출자는 먼저 ``prescription`` row를 잠가야 합니다. 이후 잠금 순서는 계약의
        ``PRESCRIPTION → AI_JOB → domain row`` 순서를 따릅니다. Track B를 포함한 다른
        domain row 무효화가 끝난 뒤 ``invalidate_version_outbox``를 호출해야 합니다.
        """
        jobs = list(
            (
                await self.session.execute(
                    select(AiJob)
                    .where(
                        AiJob.prescription_version_id == prescription_version_id,
                        AiJob.status.in_((AiJobStatus.PENDING, AiJobStatus.PROCESSING, AiJobStatus.RETRY_WAIT)),
                    )
                    .with_for_update(of=AiJob)
                )
            )
            .scalars()
            .all()
        )
        job_ids = [job.id for job in jobs]
        for job in jobs:
            job.status = AiJobStatus.STALE
            job.completed_at = invalidated_at
            if job.expected_event_id is not None:
                job.last_consumed_event_id = job.expected_event_id
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None

        if job_ids:
            attempts = list(
                (
                    await self.session.execute(
                        select(AiJobAttempt)
                        .where(
                            AiJobAttempt.ai_job_id.in_(job_ids),
                            AiJobAttempt.attempt_status == AiJobAttemptStatus.PROCESSING,
                        )
                        .with_for_update(of=AiJobAttempt)
                    )
                )
                .scalars()
                .all()
            )
            for attempt in attempts:
                attempt.attempt_status = AiJobAttemptStatus.BLOCKED
                attempt.completed_at = invalidated_at

        searches = list(
            (
                await self.session.execute(
                    select(MedicationCandidateSearch)
                    .join(
                        PrescriptionVersionMedication,
                        PrescriptionVersionMedication.id
                        == MedicationCandidateSearch.prescription_version_medication_id,
                    )
                    .where(
                        PrescriptionVersionMedication.prescription_version_id == prescription_version_id,
                        MedicationCandidateSearch.status.in_(
                            (MedicationCandidateSearchStatus.RUNNING, MedicationCandidateSearchStatus.READY)
                        ),
                    )
                    .with_for_update(of=MedicationCandidateSearch)
                )
            )
            .scalars()
            .all()
        )
        for search in searches:
            search.status = MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
            search.invalidated_at = invalidated_at
            search.finalized_at = invalidated_at

        await self.session.flush()
        return job_ids

    async def invalidate_version_outbox(self, *, stale_job_ids: Sequence[UUID]) -> None:
        """모든 domain row 무효화 뒤 STALE 전환 Job의 미발행 Outbox만 취소한다."""

        if not stale_job_ids:
            return

        outbox_events = list(
            (
                await self.session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.job_id.in_(stale_job_ids),
                        OutboxEvent.status.in_((OutboxEventStatus.PENDING, OutboxEventStatus.CLAIMED)),
                    )
                    .with_for_update(of=OutboxEvent)
                )
            )
            .scalars()
            .all()
        )
        for event in outbox_events:
            event.status = OutboxEventStatus.CANCELLED
            event.claim_token = None
            event.claim_expires_at = None
        await self.session.flush()
