from email.message import EmailMessage
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Config
from app.dependencies import services
from app.services.email_delivery import NoopEmailSender, SmtpEmailSender, SmtpEmailSenderConfig


class FakeSmtp:
    instances: list["FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent_messages: list[EmailMessage] = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def starttls(self, *, context) -> None:  # noqa: ANN001
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent_messages.append(message)


@pytest.mark.asyncio
async def test_smtp_email_sender_sends_verification_message_without_external_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSmtp.instances.clear()
    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP", FakeSmtp)

    sender = SmtpEmailSender(
        SmtpEmailSenderConfig(
            host="smtp.example.test",
            port=587,
            username="mailer@example.test",
            password="secret-password",
            from_email="no-reply@example.test",
        )
    )

    await sender.send_email_verification(email="user@example.test", token="verification-token")

    smtp = FakeSmtp.instances[0]
    assert smtp.host == "smtp.example.test"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_args == ("mailer@example.test", "secret-password")

    message = smtp.sent_messages[0]
    assert message["From"] == "no-reply@example.test"
    assert message["To"] == "user@example.test"
    assert message["Subject"] == "이메일 인증 안내"
    assert "verification-token" in message.get_content()


def test_get_email_sender_uses_noop_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.config, "EMAIL_PROVIDER", "noop")

    assert isinstance(services.get_email_sender(), NoopEmailSender)


def test_get_email_sender_uses_smtp_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(services.config, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(services.config, "SMTP_PORT", 587)
    monkeypatch.setattr(services.config, "SMTP_USERNAME", "mailer@example.test")
    monkeypatch.setattr(services.config, "SMTP_PASSWORD", "secret-password")
    monkeypatch.setattr(services.config, "SMTP_FROM_EMAIL", "no-reply@example.test")
    monkeypatch.setattr(services.config, "SMTP_USE_TLS", True)
    monkeypatch.setattr(services.config, "SMTP_TIMEOUT_SECONDS", 10.0)

    assert isinstance(services.get_email_sender(), SmtpEmailSender)


def _config_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "DB_HOST": "127.0.0.1",
        "DB_USER": "dummy",
        "DB_PASSWORD": "dummy",
        "DB_NAME": "dummy",
    }
    base.update(overrides)
    return base


def test_config_uses_noop_email_provider_by_default() -> None:
    config = Config(**_config_kwargs())

    assert config.EMAIL_PROVIDER == "noop"


def test_config_normalizes_smtp_email_provider() -> None:
    config = Config(
        **_config_kwargs(
            EMAIL_PROVIDER=" SMTP ",
            SMTP_HOST="smtp.example.test",
            SMTP_USERNAME="mailer@example.test",
            SMTP_PASSWORD="secret-password",
            SMTP_FROM_EMAIL="no-reply@example.test",
        )
    )

    assert config.EMAIL_PROVIDER == "smtp"


def test_config_rejects_smtp_provider_without_required_secret_settings() -> None:
    with pytest.raises(ValidationError, match="SMTP configuration is required"):
        Config(**_config_kwargs(EMAIL_PROVIDER="smtp", SMTP_HOST="smtp.example.test"))
