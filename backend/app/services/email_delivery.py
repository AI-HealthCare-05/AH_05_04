import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


@dataclass(frozen=True)
class EmailDeliveryMessage:
    to_email: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send_email_verification(self, *, email: str, token: str) -> None: ...

    async def send_password_reset(self, *, email: str, token: str) -> None: ...


class NoopEmailSender:
    """실제 Provider 연결 전 기본 sender입니다.

    원문 token은 이 adapter 호출 경계까지만 전달하고 저장·로그로 남기지 않습니다.
    운영 SMTP/transactional email Provider 연결은 이 인터페이스 뒤에 붙입니다.
    """

    async def send_email_verification(self, *, email: str, token: str) -> None:
        _ = EmailDeliveryMessage(
            to_email=email,
            subject="이메일 인증 안내",
            body="회원가입 이메일 인증 링크를 확인해 주세요.",
        )

    async def send_password_reset(self, *, email: str, token: str) -> None:
        _ = EmailDeliveryMessage(
            to_email=email,
            subject="비밀번호 재설정 안내",
            body="비밀번호 재설정 링크를 확인해 주세요.",
        )


@dataclass(frozen=True)
class SmtpEmailSenderConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    use_tls: bool = True
    timeout_seconds: float = 10.0


class SmtpEmailSender:
    """SMTP provider adapter.

    원문 token은 메일 본문 생성과 SMTP 전송 경계에서만 사용하고 저장·로그로 남기지 않습니다.
    Gmail SMTP, Google Workspace, SendGrid SMTP, Mailgun SMTP처럼 SMTP를 제공하는 서비스는 이 adapter 뒤에 연결합니다.
    """

    def __init__(self, smtp_config: SmtpEmailSenderConfig) -> None:
        self._smtp_config = smtp_config

    async def send_email_verification(self, *, email: str, token: str) -> None:
        message = EmailDeliveryMessage(
            to_email=email,
            subject="이메일 인증 안내",
            body=f"회원가입 이메일 인증 token입니다.\n\n{token}\n\n이 token은 제한된 시간 동안만 사용할 수 있습니다.",
        )
        await self._send(message)

    async def send_password_reset(self, *, email: str, token: str) -> None:
        message = EmailDeliveryMessage(
            to_email=email,
            subject="비밀번호 재설정 안내",
            body=f"비밀번호 재설정 token입니다.\n\n{token}\n\n요청하지 않았다면 이 메일을 무시해 주세요.",
        )
        await self._send(message)

    async def _send(self, message: EmailDeliveryMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailDeliveryMessage) -> None:
        email_message = EmailMessage()
        email_message["From"] = self._smtp_config.from_email
        email_message["To"] = message.to_email
        email_message["Subject"] = message.subject
        email_message.set_content(message.body)

        if self._smtp_config.use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(
                self._smtp_config.host,
                self._smtp_config.port,
                timeout=self._smtp_config.timeout_seconds,
            ) as server:
                server.starttls(context=context)
                server.login(self._smtp_config.username, self._smtp_config.password)
                server.send_message(email_message)
            return

        with smtplib.SMTP(
            self._smtp_config.host,
            self._smtp_config.port,
            timeout=self._smtp_config.timeout_seconds,
        ) as server:
            server.login(self._smtp_config.username, self._smtp_config.password)
            server.send_message(email_message)
