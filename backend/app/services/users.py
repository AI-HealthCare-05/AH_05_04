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
from app.services.user_consent_policy import current_consent_policy_version

PROFILE_CLEARABLE_FIELDS = {"phone_number", "birthday", "gender"}


def _user_update_data(data: UserUpdateRequest) -> dict[str, object]:
    update_data = data.model_dump(exclude_unset=True)

    for required_field in ("name", "email"):
        if update_data.get(required_field) is None:
            update_data.pop(required_field, None)

    return {key: value for key, value in update_data.items() if value is not None or key in PROFILE_CLEARABLE_FIELDS}


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
        update_data = _user_update_data(data)

        if data.email is not None:
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


def _is_currently_granted(row: UserConsent, current_policy_version: str) -> bool:
    return (
        row.status == ConsentStatus.GRANTED
        and row.policy_version == current_policy_version
        and row.granted_at is not None
        and row.withdrawn_at is None
    )


def _consent_data(purpose: ConsentPurpose, row: UserConsent | None) -> UserConsentData:
    current_policy_version = current_consent_policy_version(purpose)
    if row is None:
        return UserConsentData(
            purpose=purpose,
            status=None,
            policy_version=None,
            current_policy_version=current_policy_version,
            is_granted=False,
            granted_at=None,
            withdrawn_at=None,
            updated_at=None,
        )

    return UserConsentData(
        purpose=row.purpose,
        status=row.status,
        policy_version=row.policy_version,
        current_policy_version=current_policy_version,
        is_granted=_is_currently_granted(row, current_policy_version),
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
        current_policy_version = current_consent_policy_version(purpose)
        existing = await self.repository.get_current(user_id=user.id, purpose=purpose)

        if not current_policy_version.strip():
            if purpose == ConsentPurpose.OCR and request.status == ConsentStatus.WITHDRAWN and existing is not None:
                if request.policy_version != existing.policy_version:
                    raise ApiError(
                        status_code=422,
                        code="VALIDATION_FAILED",
                        message="동의 정책 버전을 확인해 주세요.",
                        details=[ErrorDetail(field="policy_version", reason="POLICY_VERSION_MISMATCH")],
                    )
            else:
                raise ApiError(
                    status_code=503,
                    code="CONSENT_POLICY_UNAVAILABLE",
                    message="현재 동의 안내를 사용할 수 없습니다.",
                )
        elif request.policy_version != current_policy_version:
            raise ApiError(
                status_code=422,
                code="VALIDATION_FAILED",
                message="동의 정책 버전을 확인해 주세요.",
                details=[ErrorDetail(field="policy_version", reason="POLICY_VERSION_MISMATCH")],
            )

        row = await self.repository.set_status(
            user_id=user.id,
            purpose=purpose,
            status=request.status,
            policy_version=request.policy_version,
            changed_at=datetime.now(config.TIMEZONE),
        )
        return UserConsentResponse(data=_consent_data(purpose, row))
