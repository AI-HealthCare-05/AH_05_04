from collections.abc import Iterator
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import EmailStr
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.utils.common import normalize_email
from app.models.users import Gender, User

DuplicateUserField = Literal["email", "phone_number"]

MYSQL_DUPLICATE_ENTRY_ERROR_CODE = 1062
POSTGRES_UNIQUE_VIOLATION_SQLSTATE = "23505"

# 실제 PostgreSQL schema에서 확인한 unique 인덱스·제약조건 이름입니다.
EMAIL_UNIQUE_KEY = "ix_user_email"
PHONE_NUMBER_UNIQUE_KEY = "user_phone_number_key"


class DuplicateUserFieldError(Exception):
    def __init__(self, field: DuplicateUserField) -> None:
        self.field = field
        super().__init__(f"Duplicate user field: {field}")


def _iter_database_errors(exc: IntegrityError) -> Iterator[BaseException]:
    """SQLAlchemy wrapper와 원본 DB 예외를 순서대로 확인합니다.

    asyncpg 오류는 SQLAlchemy 예외의 cause 안에 들어갈 수 있으므로
    오류 문자열이나 쿼리 파라미터를 출력하지 않고 구조화된 속성만 읽습니다.
    """
    current: BaseException | None = exc.orig
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _get_postgresql_error_metadata(
    exc: IntegrityError,
) -> tuple[str | None, str | None]:
    """PostgreSQL SQLSTATE와 constraint 이름을 민감정보 없이 추출합니다."""
    sqlstate: str | None = None
    constraint_name: str | None = None

    for error in _iter_database_errors(exc):
        sqlstate = sqlstate or getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)

        constraint_name = constraint_name or getattr(
            error,
            "constraint_name",
            None,
        )

        # psycopg 계열 예외와의 호환성을 위한 구조화된 진단 정보입니다.
        diagnostics = getattr(error, "diag", None)
        if diagnostics is not None:
            constraint_name = constraint_name or getattr(
                diagnostics,
                "constraint_name",
                None,
            )

    return sqlstate, constraint_name


# phone_number는 현재 가입·프로필 수정 어디서도 값을 받지 않아 이 분기가 실행되지 않지만,
# DB unique 제약은 유지되므로 향후 입력 경로가 다시 추가될 때도 같은 409 처리를 적용합니다.
def get_duplicate_user_field(
    exc: IntegrityError,
) -> DuplicateUserField | None:
    sqlstate, constraint_name = _get_postgresql_error_metadata(exc)

    if sqlstate == POSTGRES_UNIQUE_VIOLATION_SQLSTATE:
        if constraint_name == EMAIL_UNIQUE_KEY:
            return "email"

        if constraint_name == PHONE_NUMBER_UNIQUE_KEY:
            return "phone_number"

        # 알 수 없는 unique 제약 오류는 임의로 사용자 필드에 매핑하지 않습니다.
        return None

    # 전환 기간의 MySQL 예외 처리 호환성을 유지합니다.
    error_args = getattr(exc.orig, "args", ())
    if not error_args or error_args[0] != MYSQL_DUPLICATE_ENTRY_ERROR_CODE:
        return None

    error_message = str(error_args[1]) if len(error_args) > 1 else str(exc.orig)

    if EMAIL_UNIQUE_KEY in error_message:
        return "email"

    if "phone_number" in error_message:
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

    # phone_number/gender/birthday는 현재 signup 호출부에서 넘기지 않아 항상 None이지만,
    # 해당 값 입력이 Post-MVP로 돌아왔을 때 이 Repository 메서드까지 다시 만들지 않아도 되도록 남겨둡니다.
    async def create_user(
        self,
        email: str | EmailStr,
        hashed_password: str,
        name: str,
        phone_number: str | None = None,
        gender: Gender | None = None,
        birthday: date | None = None,
        *,
        is_active: bool = True,
        is_admin: bool = False,
    ) -> User:
        user = User(
            # 동시 가입과 API 우회를 포함한 모든 저장 경로에서
            # PostgreSQL unique 비교 전에 이메일을 소문자로 통일합니다.
            email=normalize_email(str(email)),
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
        normalized_email = normalize_email(email)
        result = await self.session.execute(select(User).where(User.email == normalized_email))
        return result.scalar_one_or_none()

    async def exists_by_email(
        self,
        email: str | EmailStr,
    ) -> bool:
        normalized_email = normalize_email(str(email))
        result = await self.session.scalar(select(exists().where(User.email == normalized_email)))
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
            if key not in ALLOWED_UPDATE_FIELDS or value is None:
                continue

            if key == "email":
                # 프로필 수정 경로에서도 회원가입과 같은 저장 규칙을 적용합니다.
                value = normalize_email(str(value))

            setattr(user, key, value)

        try:
            # 사전 중복 조회 이후 발생할 수 있는 동시 수정 경쟁도
            # DB unique 제약을 기준으로 다시 검증합니다.
            await self.session.flush()
        except IntegrityError as exc:
            duplicate_field = get_duplicate_user_field(exc)

            # 이메일·전화번호 이외의 무결성 오류는 원인을 숨기지 않고
            # 기존 예외 처리 계층으로 전달합니다.
            if duplicate_field is None:
                raise

            raise DuplicateUserFieldError(
                duplicate_field,
            ) from exc

        return user
