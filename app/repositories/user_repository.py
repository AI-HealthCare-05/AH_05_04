from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import EmailStr
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.users import Gender, User

DuplicateUserField = Literal["email", "phone_number"]

MYSQL_DUPLICATE_ENTRY_ERROR_CODE = 1062
EMAIL_UNIQUE_KEY = "ix_user_email"
PHONE_NUMBER_UNIQUE_KEY = "phone_number"


class DuplicateUserFieldError(Exception):
    def __init__(self, field: DuplicateUserField) -> None:
        self.field = field
        super().__init__(f"Duplicate user field: {field}")


def get_duplicate_user_field(
    exc: IntegrityError,
) -> DuplicateUserField | None:
    error_args = getattr(exc.orig, "args", ())

    if not error_args:
        return None

    if error_args[0] != MYSQL_DUPLICATE_ENTRY_ERROR_CODE:
        return None

    error_message = str(error_args[1]) if len(error_args) > 1 else str(exc.orig)

    if EMAIL_UNIQUE_KEY in error_message:
        return "email"

    if PHONE_NUMBER_UNIQUE_KEY in error_message:
        return "phone_number"

    return None


ALLOWED_UPDATE_FIELDS = {
    "name",
    "email",
    "phone_number",
    "gender",
    "birthday",
}


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self) -> list[User]:
        result = await self.session.execute(select(User))
        return list(result.scalars().all())

    async def get_user(
        self,
        user_id: UUID,
    ) -> User | None:
        return await self.session.get(User, user_id)

    async def create_user(
        self,
        email: str | EmailStr,
        hashed_password: str,
        name: str,
        phone_number: str,
        gender: Gender,
        birthday: date,
        *,
        is_active: bool = True,
        is_admin: bool = False,
    ) -> User:
        user = User(
            email=str(email),
            hashed_password=hashed_password,
            name=name,
            phone_number=phone_number,
            gender=gender,
            birthday=birthday,
            is_active=is_active,
            is_admin=is_admin,
        )

        self.session.add(user)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            duplicate_field = get_duplicate_user_field(exc)

            if duplicate_field is None:
                raise

            raise DuplicateUserFieldError(
                duplicate_field,
            ) from exc

        return user

    async def get_user_by_email(
        self,
        email: str,
    ) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def exists_by_email(
        self,
        email: str | EmailStr,
    ) -> bool:
        result = await self.session.scalar(select(exists().where(User.email == str(email))))
        return bool(result)

    async def exists_by_phone_number(
        self,
        phone_number: str,
    ) -> bool:
        result = await self.session.scalar(
            select(
                exists().where(
                    User.phone_number == phone_number,
                )
            )
        )
        return bool(result)

    async def update_last_login(
        self,
        user_id: UUID,
    ) -> None:
        user = await self.get_user(user_id)

        if user is not None:
            user.last_login = datetime.now(config.TIMEZONE)
            await self.session.flush()

    async def update_instance(
        self,
        user: User,
        data: dict[str, Any],
    ) -> User:
        for key, value in data.items():
            if key in ALLOWED_UPDATE_FIELDS and value is not None:
                setattr(user, key, value)

        await self.session.flush()
        return user
