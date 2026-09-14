from datetime import datetime

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.core.utils.common import normalize_email
from app.dtos.users import (
    UserConsentData,
    UserConsentListResponse,
    UserConsentResponse,
    UserConsentUpdateRequest,
    UserUpdateRequest,
)
from app.models.user_consents import ConsentPurpose, ConsentStatus, UserConsent
from app.models.users import User
from app.repositories.user_consent_repository import UserConsentRepository
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
from app.services.auth import AuthService


class UserManageService:
    def __init__(
        self,
        repository: UserRepository,
        auth_service: AuthService,
    ) -> None:
        self.repo = repository
        self.auth_service = auth_service

    async def update_user(
        self,
        user: User,
        data: UserUpdateRequest,
    ) -> User:
        update_data = data.model_dump(exclude_none=True)

        if data.email:
            normalized_email = normalize_email(str(data.email))

            if normalized_email != user.email:
                await self.auth_service.check_email_exists(normalized_email)
                update_data["email"] = normalized_email

        try:
            return await self.repo.update_instance(
                user=user,
                data=update_data,
            )
        except DuplicateUserFieldError as exc:
            # 사전 중복 조회를 동시에 통과한 요청도 DB unique 제약에서
            # 충돌하면 회원가입과 동일한 409 계약으로 변환합니다.
            if exc.field == "email":
                message = "이미 사용중인 이메일입니다."
            else:
                message = "이미 사용중인 휴대폰 번호입니다."

            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message=message,
                details=[
                    ErrorDetail(
                        field=exc.field,
                        reason="ALREADY_EXISTS",
                    )
                ],
            ) from exc


def _consent_data(purpose: ConsentPurpose, row: UserConsent | None) -> UserConsentData:
    if row is None:
        return UserConsentData(
            purpose=purpose,
            status=None,
            policy_version=None,
            is_granted=False,
            granted_at=None,
            withdrawn_at=None,
            updated_at=None,
        )

    return UserConsentData(
        purpose=row.purpose,
        status=row.status,
        policy_version=row.policy_version,
        is_granted=row.status == ConsentStatus.GRANTED and row.granted_at is not None and row.withdrawn_at is None,
        granted_at=row.granted_at,
        withdrawn_at=row.withdrawn_at,
        updated_at=row.updated_at,
    )


class UserConsentService:
    def __init__(self, repository: UserConsentRepository) -> None:
        self.repository = repository

    async def list_user_consents(self, *, user: User) -> UserConsentListResponse:
        rows = await self.repository.list_current_for_user(user_id=user.id)
        rows_by_purpose = {row.purpose: row for row in rows}
        return UserConsentListResponse(
            data=[_consent_data(purpose, rows_by_purpose.get(purpose)) for purpose in ConsentPurpose]
        )

    async def set_user_consent(
        self,
        *,
        user: User,
        purpose: ConsentPurpose,
        request: UserConsentUpdateRequest,
    ) -> UserConsentResponse:
        row = await self.repository.set_status(
            user_id=user.id,
            purpose=purpose,
            status=request.status,
            policy_version=request.policy_version,
            changed_at=datetime.now(config.TIMEZONE),
        )
        return UserConsentResponse(data=_consent_data(purpose, row))
