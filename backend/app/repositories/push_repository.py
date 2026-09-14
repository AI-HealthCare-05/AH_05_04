from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.notifications import NotificationRecord
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile
from app.models.push import PushDelivery, PushSubscription
from app.models.users import User


class PushRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_user(self, user_id: UUID) -> User | None:
        return await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
        )

    async def profile_id(self, user_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(Profile.id).where(Profile.user_id == user_id, Profile.profile_type == "SELF")
        )

    async def lock_subscription(self, subscription_id: UUID) -> PushSubscription | None:
        return await self.session.scalar(
            select(PushSubscription)
            .where(PushSubscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def by_endpoint(self, digest: str) -> PushSubscription | None:
        return await self.session.scalar(
            select(PushSubscription)
            .where(PushSubscription.endpoint_hmac == digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def insert_subscription(self, **values) -> None:
        await self.session.execute(
            insert(PushSubscription).values(**values).on_conflict_do_nothing(constraint="uq_push_subscription_endpoint")
        )

    async def cancel_pending(self, subscription_id: UUID, now: datetime) -> None:
        # SENDING is retained until its result/lease is known, never reclaimed for a new send.
        await self.session.execute(
            update(PushDelivery)
            .where(PushDelivery.subscription_id == subscription_id, PushDelivery.status == "PENDING")
            .values(status="CANCELLED", failure_reason="SUBSCRIPTION_REVOKED", updated_at=now)
        )

    async def revoke(self, subscription: PushSubscription, now: datetime) -> None:
        subscription.revoked_at = subscription.revoked_at or now
        subscription.ciphertext = None
        await self.cancel_pending(subscription.id, now)
        await self.session.flush()

    async def revoke_for_user(self, user_id: UUID, now: datetime) -> None:
        """Caller holds the user lock; invalidate all browser secrets in that transaction."""
        subscriptions = await self.session.scalars(
            select(PushSubscription)
            .where(
                PushSubscription.profile_id.in_(select(Profile.id).where(Profile.user_id == user_id)),
                PushSubscription.revoked_at.is_(None),
            )
            .order_by(PushSubscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for subscription in subscriptions:
            await self.revoke(subscription, now)

    async def generate(self, now: datetime, limit: int) -> int:
        query = (
            select(NotificationRecord, PushSubscription, MedicationOccurrence.confirmation_deadline_at)
            .join(MedicationOccurrence, MedicationOccurrence.id == NotificationRecord.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .join(Profile, Profile.id == Prescription.profile_id)
            .join(User, User.id == Profile.user_id)
            .join(PushSubscription, PushSubscription.profile_id == Profile.id)
            .where(
                Profile.profile_type == "SELF",
                User.is_active.is_(True),
                User.account_status == "ACTIVE",
                PushSubscription.token_version == User.token_version,
                PushSubscription.revoked_at.is_(None),
                NotificationRecord.status == "DELIVERED",
                NotificationRecord.delivered_at >= PushSubscription.activated_at,
                NotificationRecord.delivered_at > now - timedelta(seconds=300),
                MedicationOccurrence.status == "PENDING",
                MedicationOccurrence.confirmation_deadline_at > now,
                ~exists(select(MedicationCheckin.id).where(MedicationCheckin.occurrence_id == MedicationOccurrence.id)),
                ~exists(
                    select(PushDelivery.id).where(
                        PushDelivery.notification_id == NotificationRecord.id,
                        PushDelivery.subscription_id == PushSubscription.id,
                        PushDelivery.generation == PushSubscription.generation,
                    )
                ),
            )
            .order_by(NotificationRecord.delivered_at, NotificationRecord.id, PushSubscription.id)
            .limit(limit)
        )
        count = 0
        for notification, subscription, deadline in (await self.session.execute(query)).all():
            row = await self.session.scalar(
                insert(PushDelivery)
                .values(
                    id=uuid4(),
                    notification_id=notification.id,
                    subscription_id=subscription.id,
                    generation=subscription.generation,
                    status="PENDING",
                    attempt_count=0,
                    next_attempt_at=now,
                    expires_at=min(deadline, notification.delivered_at + timedelta(seconds=300)),
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_push_delivery_generation")
                .returning(PushDelivery.id)
            )
            count += row is not None
        return count

    async def candidate_ids(self, now: datetime, limit: int) -> list[UUID]:
        return list(
            await self.session.scalars(
                select(PushDelivery.id)
                .where(PushDelivery.status == "PENDING", PushDelivery.next_attempt_at <= now)
                .order_by(PushDelivery.next_attempt_at, PushDelivery.id)
                .limit(limit)
            )
        )

    async def context(self, delivery_id: UUID) -> tuple[UUID, UUID, UUID] | None:
        row = (
            await self.session.execute(
                select(Profile.user_id, NotificationRecord.occurrence_id, PushSubscription.id)
                .select_from(PushDelivery)
                .join(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
                .join(Profile, Profile.id == PushSubscription.profile_id)
                .join(NotificationRecord, NotificationRecord.id == PushDelivery.notification_id)
                .where(PushDelivery.id == delivery_id)
            )
        ).first()
        return None if row is None else (row[0], row[1], row[2])

    async def lock_delivery(self, delivery_id: UUID) -> PushDelivery | None:
        return await self.session.scalar(
            select(PushDelivery)
            .where(PushDelivery.id == delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def invalid_subscription_ids(self, limit: int) -> list[tuple[UUID, UUID]]:
        rows = await self.session.execute(
            select(User.id, PushSubscription.id)
            .join(Profile, Profile.user_id == User.id)
            .join(PushSubscription, PushSubscription.profile_id == Profile.id)
            .where(
                PushSubscription.revoked_at.is_(None),
                or_(
                    User.token_version != PushSubscription.token_version,
                    User.is_active.is_(False),
                    User.account_status != "ACTIVE",
                ),
            )
            .order_by(User.id, PushSubscription.id)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in rows]

    async def cleanup(self, now: datetime, limit: int) -> None:
        # Ledger-only updates never acquire subscription locks afterwards.
        expired = (
            select(PushDelivery.id)
            .where(PushDelivery.status == "SENDING", PushDelivery.claim_expires_at <= now)
            .order_by(PushDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        await self.session.execute(
            update(PushDelivery)
            .where(PushDelivery.id.in_(expired))
            .values(
                status="UNKNOWN",
                failure_reason="CLAIM_EXPIRED",
                claim_token=None,
                claim_expires_at=None,
                updated_at=now,
            )
        )
        old = (
            select(PushDelivery.id)
            .where(
                PushDelivery.status.not_in(("PENDING", "SENDING")), PushDelivery.updated_at < now - timedelta(days=7)
            )
            .limit(limit)
        )
        await self.session.execute(delete(PushDelivery).where(PushDelivery.id.in_(old)))

    async def purge_subscriptions(self, now: datetime, limit: int) -> None:
        # Endpoint tombstones remain until all delivery attempts are terminal and removed.
        revoked = (
            select(PushSubscription.id)
            .where(
                PushSubscription.revoked_at < now - timedelta(days=7),
                ~exists(select(PushDelivery.id).where(PushDelivery.subscription_id == PushSubscription.id)),
            )
            .limit(limit)
        )
        await self.session.execute(delete(PushSubscription).where(PushSubscription.id.in_(revoked)))
