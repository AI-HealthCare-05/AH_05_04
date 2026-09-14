"""Web Push encryption with a pinned, redirect-free HTTPS transport."""

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from py_vapid import Vapid02  # type: ignore[import-untyped]
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]
from requests import Response  # type: ignore[import-untyped]

from app.core.push import PushSettings


class UnsafePushEndpointError(ValueError):
    def __init__(self) -> None:
        super().__init__("Push endpoint is not allowed")


def validate_endpoint(endpoint: str, allowed_hosts: list[str]) -> str:
    try:
        parsed = urlsplit(endpoint)
        if (
            len(endpoint) > 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in endpoint)
            or "\\" in endpoint
            or parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.netloc not in (parsed.hostname, f"{parsed.hostname}:443")
            or parsed.port not in (None, 443)
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise UnsafePushEndpointError()
        assert parsed.hostname is not None
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return parsed.hostname
        raise UnsafePushEndpointError()
    except ValueError:
        raise UnsafePushEndpointError() from None


def resolve_endpoint(endpoint: str, allowed_hosts: list[str]) -> tuple[str, str]:
    host = validate_endpoint(endpoint, allowed_hosts)
    addresses = [str(entry[4][0]) for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    if not addresses or any(
        not ipaddress.ip_address(address).is_global
        or ipaddress.ip_address(address).is_multicast
        or ipaddress.ip_address(address).is_reserved
        for address in addresses
    ):
        raise UnsafePushEndpointError()
    return host, addresses[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, 443, timeout=timeout, context=self.tls_context)
        self.address = address
        self.deadline = time.monotonic() + timeout

    def connect(self) -> None:
        # The TCP destination is numeric; TLS still verifies the original hostname.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            if time.monotonic() >= self.deadline:
                raise TimeoutError()
            raw.settimeout(self.deadline - time.monotonic())
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
            if time.monotonic() >= self.deadline:
                self.sock.close()
                raise TimeoutError()
            self.sock.settimeout(self.deadline - time.monotonic())
        except BaseException:
            raw.close()
            raise


class PinnedPushSession:
    def __init__(self, allowed_hosts: list[str], deadline: float | None = None) -> None:
        self.allowed_hosts = allowed_hosts
        self.deadline = deadline

    def post(self, url: str, data: bytes, headers: dict[str, str], timeout: float, **kwargs: Any) -> Response:
        host, address = resolve_endpoint(url, self.allowed_hosts)
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
            if timeout <= 0:
                raise TimeoutError()
        parsed = urlsplit(url)
        connection = PinnedHTTPSConnection(host, address, timeout)
        timer: threading.Timer | None = None
        try:
            connection.connect()

            def interrupt_read() -> None:
                sock = connection.sock
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            # A peer trickling HTTP headers must not extend the total deadline.
            remaining = timeout if self.deadline is None else self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            timer = threading.Timer(remaining, interrupt_read)
            timer.daemon = True
            timer.start()
            connection.request("POST", parsed.path + (f"?{parsed.query}" if parsed.query else ""), data, headers)
            result = connection.getresponse()
            response = Response()
            response.status_code = result.status
            response.headers["Retry-After"] = result.getheader("Retry-After", "")
            # Provider bodies/URLs/reason phrases never enter exception or log text.
            response._content = b""
            return response
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()


@dataclass(frozen=True)
class PushSendResult:
    status: str
    reason: str | None = None
    retry_after_seconds: int = 0


def retry_after_seconds(value: str, now: datetime) -> int:
    try:
        if value.isdecimal():
            return min(int(value), 86400)
        parsed = parsedate_to_datetime(value)
        return max(0, min(86400, int((parsed.astimezone(UTC) - now).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        return 0


def send_push(
    settings: PushSettings, subscription: str, payload: dict[str, str], ttl: int, *, deadline: float | None = None
) -> PushSendResult:
    try:
        vapid = Vapid02.from_pem(settings.vapid_private_key.get_secret_value().encode())
        session = PinnedPushSession(settings.allowed_hosts, deadline)
        try:
            response = webpush(
                json.loads(subscription),
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=ttl,
                timeout=10,
                requests_session=session,
            )
        except WebPushException as exc:
            if exc.response is None:
                return PushSendResult("FAILED", "ENCODING_ERROR")
            response = exc.response
        if not isinstance(response, Response):
            return PushSendResult("FAILED", "ENCODING_ERROR")
        status = response.status_code
        if 200 <= status < 300:
            return PushSendResult("ACCEPTED")
        if status in (404, 410):
            return PushSendResult("FAILED", "SUBSCRIPTION_EXPIRED")
        if status == 429 or 500 <= status < 600:
            return PushSendResult(
                "PENDING",
                "PROVIDER_RETRY",
                retry_after_seconds(response.headers.get("Retry-After", ""), datetime.now(UTC)),
            )
        return PushSendResult("FAILED", "PROVIDER_REJECTED")
    except UnsafePushEndpointError:
        return PushSendResult("FAILED", "ENDPOINT_BLOCKED")
    except (OSError, http.client.HTTPException):
        return PushSendResult("UNKNOWN", "TRANSPORT_UNKNOWN")
    except Exception:
        return PushSendResult("FAILED", "ENCODING_ERROR")
