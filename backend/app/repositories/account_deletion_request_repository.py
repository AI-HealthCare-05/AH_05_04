from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus
from app.repositories.medication_candidate_repository import MedicationCandidateRepository

FAILED_DEMO_DELETION_CODE = "DEMO_DELETION_FAILED"
WITHDRAWN_PROFILE_NAME = "withdrawn"
CANDIDATE_CLEANUP_MARKER = "__candidate_cleanup__"


class AccountDeletionRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(self, *, user_id: UUID, requested_at: datetime) -> AccountDeletionRequest:
        request = AccountDeletionRequest(
            user_id=user_id,
            status=AccountDeletionRequestStatus.PENDING,
            requested_at=requested_at,
        )
        self.session.add(request)
        await self.session.flush()
        return request

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

        try:
            async with self.session.begin_nested():
                await self._delete_user_owned_runtime_data(request.user_id)
                await self._anonymize_withdrawn_user(
                    user_id=request.user_id,
                    anonymized_email=anonymized_email,
                    disabled_password_hash=disabled_password_hash,
                    withdrawn_at=completed_at,
                )
        except Exception:
            request.status = AccountDeletionRequestStatus.FAILED
            request.failed_at = completed_at
            request.last_error_code = FAILED_DEMO_DELETION_CODE
            request.retry_count += 1
            await self.session.flush()
            raise

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
        await self.session.execute(text(statement), params)

    async def _delete_user_owned_runtime_data(self, user_id: UUID) -> None:
        params = {"user_id": str(user_id)}
        for statement in USER_DATA_DELETE_STATEMENTS:
            if statement == CANDIDATE_CLEANUP_MARKER:
                await MedicationCandidateRepository(self.session).delete_for_account_withdrawal(user_id=user_id)
                continue
            await self._execute(statement, **params)

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
