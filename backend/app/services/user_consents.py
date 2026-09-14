from datetime import UTC, datetime
from typing import Literal

from app.core.errors import ApiError
from app.dtos.user_consents import OcrConsentState
from app.models.user_consents import ConsentPurpose, ConsentStatus, UserConsent
from app.models.users import User
from app.repositories.user_consent_repository import UserConsentRepository


class OcrConsentService:
    def __init__(self, repository: UserConsentRepository, *, current_policy_version: str) -> None:
        self._repository = repository
        self._current_policy_version = current_policy_version

    def _configured_version(self) -> str:
        if not self._current_policy_version.strip():
            raise ApiError(
                status_code=503, code="CONSENT_POLICY_UNAVAILABLE", message="현재 동의 안내를 사용할 수 없습니다."
            )
        return self._current_policy_version

    @staticmethod
    def _require_active_user(user: User) -> None:
        if user.account_status != "ACTIVE" or not user.is_active:
            raise ApiError(status_code=403, code="CONSENT_REQUIRED", message="처방전 처리 동의가 필요합니다.")

    def _state(self, row: UserConsent | None, version: str) -> OcrConsentState:
        if row is None:
            return OcrConsentState(
                status="MISSING",
                effective=False,
                reason="MISSING_CONSENT",
                current_policy_version=version,
                accepted_policy_version=None,
                granted_at=None,
                withdrawn_at=None,
            )
        valid = (
            row.status == ConsentStatus.GRANTED
            and row.policy_version == version
            and row.granted_at is not None
            and row.withdrawn_at is None
        )
        reason: Literal["WITHDRAWN", "POLICY_VERSION_MISMATCH"] | None = None
        if not valid:
            reason = "WITHDRAWN" if row.status == ConsentStatus.WITHDRAWN else "POLICY_VERSION_MISMATCH"
        return OcrConsentState(
            status=row.status.value,
            effective=valid,
            reason=reason,
            current_policy_version=version,
            accepted_policy_version=row.policy_version,
            granted_at=row.granted_at,
            withdrawn_at=row.withdrawn_at,
        )

    async def get_state(self, *, user: User) -> OcrConsentState:
        self._require_active_user(user)
        version = self._configured_version()
        try:
            row = await self._repository.get_current(user_id=user.id, purpose=ConsentPurpose.OCR)
        except Exception:
            raise ApiError(
                status_code=503, code="CONSENT_LOOKUP_FAILED", message="동의를 확인할 수 없습니다."
            ) from None
        return self._state(row, version)

    async def require_for_intake(self, *, user: User) -> None:
        state = await self.get_state(user=user)
        if not state.effective:
            raise ApiError(status_code=403, code="CONSENT_REQUIRED", message="처방전 처리 동의가 필요합니다.")

    async def grant(self, *, user: User, policy_version: str) -> OcrConsentState:
        self._require_active_user(user)
        version = self._configured_version()
        if policy_version != version:
            raise ApiError(
                status_code=409, code="CONSENT_POLICY_MISMATCH", message="현재 동의 안내를 다시 확인해 주세요."
            )
        row = await self._repository.set_status(
            user_id=user.id,
            purpose=ConsentPurpose.OCR,
            status=ConsentStatus.GRANTED,
            policy_version=version,
            changed_at=datetime.now(UTC),
        )
        return self._state(row, version)

    async def withdraw(self, *, user: User) -> OcrConsentState:
        self._require_active_user(user)
        # 정책 설정 장애는 새 외부 처리를 차단하지만, 이미 저장된 동의의 철회를 막지 않습니다.
        version = self._current_policy_version
        try:
            row = await self._repository.get_current(user_id=user.id, purpose=ConsentPurpose.OCR)
        except Exception:
            raise ApiError(
                status_code=503, code="CONSENT_LOOKUP_FAILED", message="동의를 확인할 수 없습니다."
            ) from None
        if row is None:
            return self._state(None, version)
        if row.status == ConsentStatus.WITHDRAWN:
            return self._state(row, version)
        withdrawn = await self._repository.set_status(
            user_id=user.id,
            purpose=ConsentPurpose.OCR,
            status=ConsentStatus.WITHDRAWN,
            policy_version=row.policy_version,
            changed_at=datetime.now(UTC),
        )
        return self._state(withdrawn, version)
