import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import ApiError
from app.core.push import PushSettings
from app.dtos.push import PushSubscriptionData, PushSubscriptionRequest, PushSubscriptionResponse
from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence
from app.models.users import User
from app.repositories.notification_repository import NotificationRepository
from app.repositories.push_repository import PushRepository
from app.services.push_transport import PushSendResult, UnsafePushEndpointError, resolve_endpoint


def utc_now() -> datetime:
    return datetime.now(UTC)


def unavailable() -> ApiError:
    return ApiError(status_code=503, code="PUSH_UNAVAILABLE", message="푸시 알림을 사용할 수 없습니다.")


def active(user: User, token_version: int) -> bool:
    return user.is_active and user.account_status == "ACTIVE" and user.token_version == token_version


class PushSubscriptionService:
    def __init__(
        self, repository: PushRepository, settings: PushSettings, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.clock = clock

    async def upsert(
        self, user_id: UUID, token_version: int, request: PushSubscriptionRequest
    ) -> PushSubscriptionResponse:
        if not self.settings.enabled:
            raise unavailable()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(resolve_endpoint, request.endpoint, self.settings.allowed_hosts), timeout=5
            )
        except UnsafePushEndpointError:
            raise ApiError(
                status_code=422, code="VALIDATION_FAILED", message="푸시 구독 주소를 확인해 주세요."
            ) from None
        except (OSError, TimeoutError):
            raise unavailable() from None
        repo = self.repository
        user = await repo.lock_user(user_id)
        if user is None or not active(user, token_version):
            raise ApiError(status_code=401, code="INVALID_TOKEN", message="다시 로그인해 주세요.")
        profile_id = await repo.profile_id(user_id)
        if profile_id is None:
            raise ApiError(status_code=404, code="PROFILE_NOT_FOUND", message="프로필을 찾을 수 없습니다.")
        now = self.clock()
        digest = self.settings.digest(request.endpoint)
        await repo.insert_subscription(
            id=uuid4(),
            profile_id=profile_id,
            token_version=token_version,
            generation=uuid4(),
            endpoint_hmac=digest,
            ciphertext=self.settings.encrypt(request.model_dump_json()),
            key_id=self.settings.active_key_id,
            activated_at=now,
        )
        subscription = await repo.by_endpoint(digest)
        assert subscription is not None
        if subscription.profile_id != profile_id:
            raise ApiError(
                status_code=409, code="PUSH_SUBSCRIPTION_CONFLICT", message="브라우저에서 구독을 새로 만들어 주세요."
            )
        try:
            same = (
                subscription.ciphertext is not None
                and json.loads(self.settings.decrypt(subscription.key_id, subscription.ciphertext))
                == request.model_dump()
            )
        except Exception:
            raise unavailable() from None
        if not same or subscription.revoked_at is not None or subscription.token_version != token_version:
            await repo.cancel_pending(subscription.id, now)
            subscription.generation = uuid4()
            subscription.token_version = token_version
            subscription.ciphertext = self.settings.encrypt(request.model_dump_json())
            subscription.key_id = self.settings.active_key_id
            subscription.activated_at = now
            subscription.revoked_at = None
        elif subscription.key_id != self.settings.active_key_id:
            subscription.ciphertext = self.settings.encrypt(request.model_dump_json())
            subscription.key_id = self.settings.active_key_id
        await repo.session.flush()
        return PushSubscriptionResponse(
            data=PushSubscriptionData(id=subscription.id, generation=subscription.generation)
        )

    async def revoke(self, user_id: UUID, token_version: int, subscription_id: UUID) -> None:
        repo = self.repository
        user = await repo.lock_user(user_id)
        if user is None or not active(user, token_version):
            raise ApiError(status_code=401, code="INVALID_TOKEN", message="다시 로그인해 주세요.")
        profile_id = await repo.profile_id(user_id)
        subscription = await repo.lock_subscription(subscription_id)
        if subscription is None or subscription.profile_id != profile_id:
            raise ApiError(status_code=404, code="PUSH_SUBSCRIPTION_NOT_FOUND", message="구독을 찾을 수 없습니다.")
        await repo.revoke(subscription, self.clock())


@dataclass(frozen=True)
class PushClaim:
    delivery_id: UUID
    subscription_id: UUID
    generation: UUID
    token: UUID
    key_id: str
    ciphertext: bytes
    payload: dict[str, str]
    expires_at: datetime


class PushDeliveryService:
    def __init__(self, repository: PushRepository) -> None:
        self.repository = repository

    async def prepare(self, delivery_id: UUID, *, clock: Callable[[], datetime] = utc_now) -> PushClaim | None:
        repo = self.repository
        context = await repo.context(delivery_id)
        if context is None:
            return None
        user_id, occurrence_id, subscription_id = context
        user = await repo.lock_user(user_id)
        occurrence = await repo.session.scalar(
            select(MedicationOccurrence)
            .where(MedicationOccurrence.id == occurrence_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = await repo.lock_subscription(subscription_id)
        delivery = await repo.lock_delivery(delivery_id)
        now = clock()
        if delivery is None or delivery.status != "PENDING" or delivery.next_attempt_at > now:
            return None
        notification = await NotificationRepository(repo.session).get_owned(
            user_id=user_id, notification_id=delivery.notification_id
        )
        checkin = await repo.session.scalar(
            select(MedicationCheckin.id).where(MedicationCheckin.occurrence_id == occurrence_id)
        )
        if (
            user is None
            or subscription is None
            or not active(user, subscription.token_version)
            or subscription.revoked_at is not None
            or subscription.ciphertext is None
            or subscription.generation != delivery.generation
            or occurrence is None
            or occurrence.status != "PENDING"
            or checkin is not None
            or now >= occurrence.confirmation_deadline_at
            or now >= delivery.expires_at
            or notification is None
            or notification[0].status != "DELIVERED"
        ):
            delivery.status = "CANCELLED"
            delivery.failure_reason = "NO_LONGER_ELIGIBLE"
            delivery.updated_at = now
            await repo.session.flush()
            return None
        token = uuid4()
        delivery.status = "SENDING"
        delivery.attempt_count += 1
        delivery.claim_token = token
        delivery.claim_expires_at = now + timedelta(seconds=30)
        delivery.updated_at = now
        await repo.session.flush()
        return PushClaim(
            delivery.id,
            subscription.id,
            delivery.generation,
            token,
            subscription.key_id,
            subscription.ciphertext,
            {
                "title": "복약 기록 알림",
                "body": "앱에서 기록을 확인해 주세요.",
                "notification_id": str(delivery.notification_id),
                "generation": str(delivery.generation),
            },
            delivery.expires_at,
        )

    async def finish(self, claim: PushClaim, result: PushSendResult, now: datetime) -> str | None:
        repo = self.repository
        subscription = await repo.lock_subscription(claim.subscription_id)
        delivery = await repo.lock_delivery(claim.delivery_id)
        if delivery is None or delivery.status != "SENDING" or delivery.claim_token != claim.token:
            return None
        if delivery.claim_expires_at is None or now >= delivery.claim_expires_at:
            result = PushSendResult("UNKNOWN", "CLAIM_EXPIRED")
        delivery.claim_token = None
        delivery.claim_expires_at = None
        delivery.updated_at = now
        delivery.status = result.status
        delivery.failure_reason = result.reason
        if result.status == "ACCEPTED":
            delivery.accepted_at = now
        elif result.status == "PENDING":
            delay = max(30 if delivery.attempt_count == 1 else 120, result.retry_after_seconds)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            if delivery.attempt_count >= 3 or delivery.next_attempt_at >= delivery.expires_at:
                delivery.status = "FAILED"
                delivery.failure_reason = "RETRY_EXHAUSTED"
            elif (
                subscription is None
                or subscription.revoked_at is not None
                or subscription.generation != claim.generation
            ):
                delivery.status = "CANCELLED"
                delivery.failure_reason = "SUBSCRIPTION_REVOKED"
        if (
            result.reason == "SUBSCRIPTION_EXPIRED"
            and subscription is not None
            and subscription.generation == claim.generation
        ):
            await repo.revoke(subscription, now)
        await repo.session.flush()
        return delivery.status

    async def revoke_invalid(self, now: datetime, limit: int) -> None:
        repo = self.repository
        for user_id, subscription_id in await repo.invalid_subscription_ids(limit):
            user = await repo.lock_user(user_id)
            subscription = await repo.lock_subscription(subscription_id)
            if subscription is not None and (user is None or not active(user, subscription.token_version)):
                await repo.revoke(subscription, now)
