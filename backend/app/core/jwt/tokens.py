from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

from app.core import config
from app.core.jwt.exceptions import ExpiredTokenError, TokenBackendError, TokenBackendExpiredError, TokenError
from app.core.jwt.state import token_backend
from app.models.users import User

if TYPE_CHECKING:
    from app.core.jwt.backends import TokenBackend


class Token:
    token_type: str | None = None
    lifetime: timedelta | None = None
    _token_backend: "TokenBackend" = token_backend

    def __init__(self, token: str | None = None, verify: bool = True) -> None:
        if not self.token_type:
            raise TokenError("token_type must be set")
        if not self.lifetime:
            raise TokenError("lifetime must be set")

        self.token = token
        self.current_time = datetime.now(tz=config.TIMEZONE)
        self.payload: dict[str, Any] = {}

        if token is not None:
            try:
                self.payload = token_backend.decode(token, verify=verify)
            except TokenBackendExpiredError as err:
                raise ExpiredTokenError("Token is expired") from err
            except TokenBackendError as err:
                raise TokenError("Token is invalid") from err

            # Access Token을 Refresh Token 자리에 쓰거나 그 반대로 쓰는 걸 막기 위해
            # 서명 검증과 별개로 클레임의 type이 이 토큰 클래스와 일치하는지 확인합니다.
            if self.payload.get("type") != self.token_type:
                raise TokenError("Token type does not match")
        else:
            self.payload = {"type": self.token_type}
            self.set_exp(from_time=self.current_time, lifetime=self.lifetime)
            self.set_jti()

    def __repr__(self) -> str:
        return repr(self.payload)

    def __getitem__(self, key: str):
        return self.payload[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.payload[key] = value

    def __delitem__(self, key: str) -> None:
        del self.payload[key]

    def __contains__(self, key: str) -> Any:
        return key in self.payload

    def __str__(self) -> str:
        """
        Signs and returns a token as a base64 encoded string.
        """
        return self._token_backend.encode(self.payload)

    def set_exp(self, from_time: datetime | None = None, lifetime: timedelta | None = None) -> None:
        if from_time is None:
            from_time = self.current_time

        if lifetime is None:
            lifetime = self.lifetime

        assert lifetime is not None

        dt = from_time + lifetime
        # `dt.timetuple()`은 tzinfo를 버리고 벽시계 값만 남기므로, 이전에는 `timegm()`이
        # `TIMEZONE`(Asia/Seoul, UTC+9)의 벽시계 값을 UTC로 오인해 모든 발급 토큰의 실제
        # 만료 시각이 의도한 값보다 항상 9시간 늦게 계산되는 버그가 있었습니다. `datetime.timestamp()`는
        # aware datetime의 tzinfo를 그대로 반영해 올바른 UTC epoch을 계산합니다.
        self.payload["exp"] = int(dt.timestamp())

    def set_jti(self) -> None:
        self.payload["jti"] = uuid4().hex

    @classmethod
    def for_user(cls, user: User) -> Self:
        token = cls()
        token["user_id"] = str(user.id)
        token["token_version"] = int(user.token_version or 0)
        return token


class AccessToken(Token):
    token_type = "access"
    lifetime = timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)


class RefreshToken(Token):
    token_type = "refresh"
    lifetime = timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES)
    no_copy_claims = ("type", "exp", "jti")

    @property
    def access_token(self) -> AccessToken:
        access = AccessToken()
        access.set_exp(from_time=self.current_time)

        no_copy = self.no_copy_claims
        for claim, value in self.payload.items():
            if claim in no_copy:
                continue
            access[claim] = value

        return access
