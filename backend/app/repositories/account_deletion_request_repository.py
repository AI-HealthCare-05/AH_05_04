from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.config import Env
from app.core.logger import default_logger
from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus
from app.models.users import AccountStatus
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.repositories.user_repository import UserRepository

FAILED_DEMO_DELETION_CODE = "DEMO_DELETION_FAILED"
WITHDRAWN_PROFILE_NAME = "withdrawn"
CANDIDATE_CLEANUP_MARKER = "__candidate_cleanup__"


CleanupSessionFactory = Callable[[], AsyncSession]


class AccountWithdrawalCredentialsChangedError(Exception):
    pass


class AccountDeletionRequestRepository:
    def __init__(self, session: AsyncSession, *, cleanup_session_factory: CleanupSessionFactory | None = None) -> None:
        self.session = session
        self._cleanup_session_factory = cleanup_session_factory

    async def create_pending(self, *, user_id: UUID, requested_at: datetime) -> AccountDeletionRequest:
        request = AccountDeletionRequest(
            user_id=user_id,
            status=AccountDeletionRequestStatus.PENDING,
            requested_at=requested_at,
        )
        self.session.add(request)
        await self.session.flush()
        return request

    async def get_latest_for_user_for_update(self, *, user_id: UUID) -> AccountDeletionRequest | None:
        result = await self.session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .order_by(AccountDeletionRequest.created_at.desc(), AccountDeletionRequest.id.desc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def request_demo_withdrawal(
        self,
        *,
        user_id: UUID,
        expected_password_hash: str,
        requested_at: datetime,
        anonymized_email: str,
        disabled_password_hash: str,
    ) -> AccountDeletionRequest | None:
        if self._cleanup_session_factory is None:
            if config.ENV is not Env.LOCAL and config.ACCOUNT_WITHDRAWAL_REQUEST_ENABLED:
                raise RuntimeError("Account withdrawal cleanup DB credentials are not configured.")
            return await self._request_demo_withdrawal_in_current_transaction(
                user_id=user_id,
                expected_password_hash=expected_password_hash,
                requested_at=requested_at,
                anonymized_email=anonymized_email,
                disabled_password_hash=disabled_password_hash,
            )

        async with self._cleanup_session_factory() as cleanup_session:
            async with cleanup_session.begin():
                cleanup_repository = AccountDeletionRequestRepository(cleanup_session)
                return await cleanup_repository._request_demo_withdrawal_in_current_transaction(
                    user_id=user_id,
                    expected_password_hash=expected_password_hash,
                    requested_at=requested_at,
                    anonymized_email=anonymized_email,
                    disabled_password_hash=disabled_password_hash,
                )

    async def _request_demo_withdrawal_in_current_transaction(
        self,
        *,
        user_id: UUID,
        expected_password_hash: str,
        requested_at: datetime,
        anonymized_email: str,
        disabled_password_hash: str,
    ) -> AccountDeletionRequest | None:
        user_repository = UserRepository(self.session)
        locked_user = await user_repository.get_user_for_update(user_id)
        if locked_user is None:
            raise AccountWithdrawalCredentialsChangedError
        if locked_user.account_status != AccountStatus.ACTIVE or not locked_user.is_active:
            return await self.get_latest_for_user_for_update(user_id=user_id)
        if locked_user.hashed_password != expected_password_hash:
            raise AccountWithdrawalCredentialsChangedError

        locked_user.account_status = AccountStatus.WITHDRAWAL_REQUESTED
        locked_user.is_active = False
        locked_user.withdrawal_requested_at = requested_at
        locked_user.token_version += 1
        request = await self.create_pending(user_id=user_id, requested_at=requested_at)
        return await self.complete_demo_withdrawal(
            request_id=request.id,
            completed_at=requested_at,
            anonymized_email=anonymized_email,
            disabled_password_hash=disabled_password_hash,
        )

    async def complete_demo_withdrawal(
        self,
        *,
        request_id: UUID,
        completed_at: datetime,
        anonymized_email: str,
        disabled_password_hash: str,
    ) -> AccountDeletionRequest:
        request = await self._lock_request(request_id)
        if request.status == AccountDeletionRequestStatus.COMPLETED:
            return request

        request.status = AccountDeletionRequestStatus.IN_PROGRESS
        request.started_at = request.started_at or completed_at
        request.failed_at = None
        request.last_error_code = None
        await self.session.flush()

        document_object_keys = await self._list_medical_document_object_keys(request.user_id)

        cleanup_step = "medical_document_object_delete"
        try:
            async with self.session.begin_nested():
                self._delete_medical_document_objects(document_object_keys)
                cleanup_step = "user_owned_runtime_data_delete"
                await self._delete_user_owned_runtime_data(request.user_id)
                cleanup_step = "withdrawn_user_anonymize"
                await self._anonymize_withdrawn_user(
                    user_id=request.user_id,
                    anonymized_email=anonymized_email,
                    disabled_password_hash=disabled_password_hash,
                    withdrawn_at=completed_at,
                )
        except Exception as exc:
            default_logger.error(
                "account_withdrawal_cleanup status=failed cleanup_step=%s failure_code=%s exception_type=%s",
                cleanup_step,
                FAILED_DEMO_DELETION_CODE,
                type(exc).__name__,
            )
            request.status = AccountDeletionRequestStatus.FAILED
            request.failed_at = completed_at
            request.last_error_code = FAILED_DEMO_DELETION_CODE
            request.retry_count += 1
            await self.session.flush()
            return request

        request.status = AccountDeletionRequestStatus.COMPLETED
        request.completed_at = completed_at
        await self.session.flush()
        return request

    async def _lock_request(self, request_id: UUID) -> AccountDeletionRequest:
        result = await self.session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        request = result.scalar_one_or_none()
        if request is None:
            raise RuntimeError("Account deletion request was not found during finalization.")
        return request

    async def _execute(self, statement: str, **params: object) -> None:
        await self._execute_with_session(self.session, statement, **params)

    async def _execute_with_session(self, session: AsyncSession, statement: str, **params: object) -> None:
        await session.execute(text(statement), params)

    async def _list_medical_document_object_keys(self, user_id: UUID) -> list[str]:
        result = await self.session.execute(
            text(
                """
                SELECT object_key
                  FROM medical_document
                 WHERE uploaded_by = :user_id
                   AND object_key IS NOT NULL
                   AND trim(object_key) <> ''
                 ORDER BY object_key, id
                """
            ),
            {"user_id": str(user_id)},
        )
        return [str(row[0]) for row in result.fetchall()]

    def _delete_medical_document_objects(self, object_keys: list[str]) -> None:
        for object_key in object_keys:
            object_path = self._medical_document_object_path(object_key)
            if object_path.exists():
                object_path.unlink()

    def _medical_document_object_path(self, object_key: str) -> Path:
        storage_root = Path(config.STORAGE_DIR).resolve()
        object_path = (storage_root / object_key).resolve()
        object_path.relative_to(storage_root)
        return object_path

    async def _delete_user_owned_runtime_data(self, user_id: UUID) -> None:
        await self._delete_user_owned_runtime_data_with_session(self.session, user_id)

    async def _delete_user_owned_runtime_data_with_session(self, session: AsyncSession, user_id: UUID) -> None:
        params = {"user_id": str(user_id)}
        for statement in USER_DATA_DELETE_STATEMENTS:
            if statement == CANDIDATE_CLEANUP_MARKER:
                await MedicationCandidateRepository(session).delete_for_account_withdrawal(user_id=user_id)
                continue
            await self._execute_with_session(session, statement, **params)

    async def _anonymize_withdrawn_user(
        self,
        *,
        user_id: UUID,
        anonymized_email: str,
        disabled_password_hash: str,
        withdrawn_at: datetime,
    ) -> None:
        await self._execute(
            """
            UPDATE profile
               SET display_name = :display_name,
                   updated_at = :withdrawn_at
             WHERE user_id = :user_id
            """,
            user_id=str(user_id),
            display_name=WITHDRAWN_PROFILE_NAME,
            withdrawn_at=withdrawn_at,
        )
        await self._execute(
            """
            UPDATE "user"
               SET email = :anonymized_email,
                   hashed_password = :disabled_password_hash,
                   name = :display_name,
                   phone_number = NULL,
                   gender = NULL,
                   birthday = NULL,
                   is_active = false,
                   account_status = 'WITHDRAWN',
                   withdrawn_at = :withdrawn_at,
                   updated_at = :withdrawn_at
             WHERE id = :user_id
            """,
            user_id=str(user_id),
            anonymized_email=anonymized_email,
            disabled_password_hash=disabled_password_hash,
            display_name=WITHDRAWN_PROFILE_NAME,
            withdrawn_at=withdrawn_at,
        )
        await self.session.flush()


USER_DATA_DELETE_STATEMENTS = (
    "DELETE FROM refresh_session WHERE user_id = :user_id",
    "DELETE FROM password_reset_token WHERE user_id = :user_id",
    "DELETE FROM user_consent WHERE user_id = :user_id",
    "DELETE FROM retrieval_run WHERE job_id IN (SELECT id FROM ai_job WHERE user_id = :user_id)",
    """
    DELETE FROM guide_feedback
     WHERE guide_id IN (SELECT id FROM guide WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id))
    """,
    """
    DELETE FROM guide_citation
     WHERE guide_id IN (SELECT id FROM guide WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id))
    """,
    """
    DELETE FROM chat_message_feedback
     WHERE chat_message_id IN (
        SELECT cm.id FROM chat_message cm JOIN chat_session cs ON cs.id = cm.session_id
         WHERE cs.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM chat_citation
     WHERE message_id IN (
        SELECT cm.id FROM chat_message cm JOIN chat_session cs ON cs.id = cm.session_id
         WHERE cs.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM chat_message
     WHERE session_id IN (SELECT id FROM chat_session WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id))
    """,
    "DELETE FROM chat_session WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)",
    "DELETE FROM guide WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)",
    """
    DELETE FROM medication_identification
     WHERE prescription_version_medication_id IN (
        SELECT pvm.id FROM prescription_version_medication pvm
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    CANDIDATE_CLEANUP_MARKER,
    """
    DELETE FROM push_delivery
     WHERE subscription_id IN (SELECT id FROM push_subscription WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id))
        OR notification_id IN (
            SELECT nr.id FROM notification_record nr
            JOIN medication_occurrence mo ON mo.id = nr.occurrence_id
            JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
            JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
            JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
            JOIN prescription p ON p.id = pv.prescription_id
            WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
        )
    """,
    "DELETE FROM push_subscription WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)",
    """
    DELETE FROM notification_record
     WHERE occurrence_id IN (
        SELECT mo.id FROM medication_occurrence mo
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM action_plan_followup_audit
     WHERE changed_by = :user_id
        OR followup_id IN (
            SELECT apf.id FROM action_plan_followup apf
            JOIN support_action_plan sap ON sap.id = apf.support_action_plan_id
            JOIN barrier_response br ON br.id = sap.barrier_response_id
            JOIN medication_checkin mc ON mc.id = br.medication_checkin_id
            JOIN medication_occurrence mo ON mo.id = mc.occurrence_id
            JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
            JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
            JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
            JOIN prescription p ON p.id = pv.prescription_id
            WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
        )
    """,
    """
    DELETE FROM action_plan_followup
     WHERE support_action_plan_id IN (
        SELECT sap.id FROM support_action_plan sap
        JOIN barrier_response br ON br.id = sap.barrier_response_id
        JOIN medication_checkin mc ON mc.id = br.medication_checkin_id
        JOIN medication_occurrence mo ON mo.id = mc.occurrence_id
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM support_action_plan
     WHERE barrier_response_id IN (
        SELECT br.id FROM barrier_response br
        JOIN medication_checkin mc ON mc.id = br.medication_checkin_id
        JOIN medication_occurrence mo ON mo.id = mc.occurrence_id
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM barrier_response
     WHERE medication_checkin_id IN (
        SELECT mc.id FROM medication_checkin mc
        JOIN medication_occurrence mo ON mo.id = mc.occurrence_id
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM safety_assessment
     WHERE medication_checkin_id IN (
        SELECT mc.id FROM medication_checkin mc
        JOIN medication_occurrence mo ON mo.id = mc.occurrence_id
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM medication_checkin
     WHERE occurrence_id IN (
        SELECT mo.id FROM medication_occurrence mo
        JOIN medication_schedule ms ON ms.id = mo.medication_schedule_id
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM medication_occurrence
     WHERE medication_schedule_id IN (
        SELECT ms.id FROM medication_schedule ms
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM medication_schedule_time
     WHERE medication_schedule_id IN (
        SELECT ms.id FROM medication_schedule ms
        JOIN prescription_version_medication pvm ON pvm.id = ms.prescription_version_medication_id
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    """
    DELETE FROM medication_schedule
     WHERE prescription_version_medication_id IN (
        SELECT pvm.id FROM prescription_version_medication pvm
        JOIN prescription_version pv ON pv.id = pvm.prescription_version_id
        JOIN prescription p ON p.id = pv.prescription_id
        WHERE p.profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)
     )
    """,
    "DELETE FROM idempotency_record WHERE user_id = :user_id",
    "UPDATE ai_job SET expected_event_id = NULL, last_consumed_event_id = NULL WHERE user_id = :user_id",
    "DELETE FROM outbox_event WHERE job_id IN (SELECT id FROM ai_job WHERE user_id = :user_id)",
    "DELETE FROM ai_job_attempt WHERE ai_job_id IN (SELECT id FROM ai_job WHERE user_id = :user_id)",
    "DELETE FROM dlq_outbox_event WHERE quarantine_id IN (SELECT id FROM message_quarantine WHERE job_id IN (SELECT id FROM ai_job WHERE user_id = :user_id))",
    "DELETE FROM message_quarantine WHERE job_id IN (SELECT id FROM ai_job WHERE user_id = :user_id)",
    "DELETE FROM ai_job WHERE user_id = :user_id",
    "DELETE FROM medication WHERE prescription_id IN (SELECT id FROM prescription WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id))",
    "DELETE FROM prescription WHERE profile_id IN (SELECT id FROM profile WHERE user_id = :user_id)",
    """
    DELETE FROM extracted_field
     WHERE ocr_job_id IN (
        SELECT oj.id FROM ocr_job oj JOIN medical_document md ON md.id = oj.document_id
        WHERE md.uploaded_by = :user_id
     )
    """,
    "DELETE FROM ocr_job WHERE document_id IN (SELECT id FROM medical_document WHERE uploaded_by = :user_id)",
    "DELETE FROM medical_document WHERE uploaded_by = :user_id",
)
