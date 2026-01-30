from collections.abc import Iterable
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any

import jwt
from jwt import ExpiredSignatureError, InvalidAlgorithmError, PyJWTError, algorithms

from app.core import config
from app.utils.jwt.exceptions import TokenBackendError, TokenBackendExpiredError

ALLOWED_ALGORITHMS = {
    "HS256",
    "HS384",
    "HS512",
}

if TYPE_CHECKING:
    pass


class TokenBackend:
    def __init__(
        self,
        algorithm: str,
        signing_key: str = config.SECRET_KEY,
        audience: str | Iterable | None = None,
        issuer: str | None = None,
        leeway: float | int | timedelta | None = None,
    ) -> None:
        self._validate_algorithm(algorithm)
        self.algorithm = algorithm
        self.signing_key = signing_key
        self.verifying_key = signing_key
        self.audience = audience
        self.issuer = issuer
        self.leeway = leeway

    @cached_property
    def prepared_signing_key(self) -> Any:
        return self._prepare_key(self.signing_key)

    @cached_property
    def prepared_verifying_key(self) -> Any:
        return self._prepare_key(self.verifying_key)

    def _prepare_key(self, key: str | None) -> Any:
        if key is None or not getattr(jwt.PyJWS, "get_algorithm_by_name", None):
            return key
        jws_alg = jwt.PyJWS().get_algorithm_by_name(self.algorithm)
        return jws_alg.prepare_key(key)

    def _validate_algorithm(self, algorithm: str) -> None:
        if algorithm not in ALLOWED_ALGORITHMS:
            raise TokenBackendError(f"Unrecognized algorithm type '{algorithm}'")

        if algorithm in algorithms.requires_cryptography and not algorithms.has_crypto:
            raise TokenBackendError(f"You must have cryptography installed to use {algorithm}.")

    def get_leeway(self) -> timedelta:
        if self.leeway is None:
            return timedelta(seconds=0)
        elif isinstance(self.leeway, (int, float)):
            return timedelta(seconds=self.leeway)
        elif isinstance(self.leeway, timedelta):
            return self.leeway
        else:
            raise TokenBackendError(
                f"Unrecognized type '{type(self.leeway)}', 'leeway' must be of type int, float or timedelta."
            )

    def encode(self, payload: dict[str, Any]) -> str:
        jwt_payload = payload.copy()
        if self.audience is not None:
            jwt_payload["aud"] = self.audience
        if self.issuer is not None:
            jwt_payload["iss"] = self.issuer

        token = jwt.encode(
            jwt_payload,
            self.prepared_signing_key,
            algorithm=self.algorithm,
        )
        if isinstance(token, bytes):
            return token.decode("utf-8")
        return token

    def decode(self, token: str, verify: bool = True) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                self.prepared_signing_key,
                algorithms=[self.algorithm],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.get_leeway(),
                options={
                    "verify_aud": self.audience is not None,
                    "verify_signature": verify,
                },
            )
        except InvalidAlgorithmError as ex:
            raise TokenBackendError("Invalid algorithm specified") from ex
        except ExpiredSignatureError as ex:
            raise TokenBackendExpiredError("Token is expired") from ex
        except PyJWTError as ex:
            raise TokenBackendError("Token is invalid") from ex
