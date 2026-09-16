"""Opt-in Web Push configuration and encrypted subscription storage."""

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PushSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEB_PUSH_", extra="ignore")

    enabled: bool = False
    production_enabled: bool = False
    # Stage 2 롤아웃(팀/테스트 계정만) 대상 제한용 placeholder. 현재는 어디서도 참조하지 않는다.
    # 대상 제한이 실제로 필요해지면 user_id(UUID) 문자열 목록을 넣고,
    # PushDeliveryService/PushSubscriptionService 등 발송·등록 경로에서 이 값을 체크하도록
    # 연결하는 작업을 별도로 완료해야 한다. 빈 리스트를 "제한 없음"으로 두지 말고,
    # 연결 전까지는 이 필드가 아무 안전 효과가 없다는 점을 리뷰 시 명시할 것.
    production_allowed_user_ids: list[str] = []
    allowed_hosts: list[str] = []
    vapid_private_key: SecretStr = SecretStr("")
    vapid_public_key: str = ""
    vapid_subject: str = ""
    encryption_keys: dict[str, SecretStr] = {}
    active_key_id: str = ""
    endpoint_hmac_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_enabled(self) -> "PushSettings":
        if not self.enabled:
            return self
        if (
            not self.allowed_hosts
            or any(not host or host != host.lower() or any(c in host for c in "/:*@") for host in self.allowed_hosts)
            or not self.vapid_private_key.get_secret_value()
            or not self.vapid_public_key
            or not self.vapid_subject.startswith("mailto:")
            or len(self.endpoint_hmac_key.get_secret_value()) < 32
            or self.active_key_id not in self.encryption_keys
            or len(self.active_key_id) > 40
        ):
            raise ValueError("Web Push configuration is incomplete")
        for key in self.encryption_keys.values():
            Fernet(key.get_secret_value().encode())
        private = serialization.load_pem_private_key(self.vapid_private_key.get_secret_value().encode(), password=None)
        if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
            raise ValueError("Web Push requires a P-256 VAPID key")
        public = (
            base64.urlsafe_b64encode(
                private.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            )
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(public, self.vapid_public_key):
            raise ValueError("Web Push VAPID key pair mismatch")
        return self

    def digest(self, endpoint: str) -> str:
        return hmac.new(
            self.endpoint_hmac_key.get_secret_value().encode(), endpoint.encode(), hashlib.sha256
        ).hexdigest()

    def encrypt(self, plaintext: str) -> bytes:
        return Fernet(self.encryption_keys[self.active_key_id].get_secret_value().encode()).encrypt(plaintext.encode())

    def decrypt(self, key_id: str, ciphertext: bytes) -> str:
        return Fernet(self.encryption_keys[key_id].get_secret_value().encode()).decrypt(ciphertext).decode()


@lru_cache
def get_push_settings() -> PushSettings:
    return PushSettings()
