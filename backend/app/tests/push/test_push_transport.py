import base64
import json
from datetime import timedelta
from unittest.mock import Mock

import http_ece  # type: ignore[import-untyped]
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.core.push import PushSettings
from app.services import push_transport
from app.services.push_transport import (
    PinnedHTTPSConnection,
    PinnedPushSession,
    UnsafePushEndpointError,
    resolve_endpoint,
    retry_after_seconds,
    send_push,
    validate_endpoint,
)
from app.tests.push.conftest import NOW, encoded_public


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://push.example.test/a",
        "https://push.example.test:444/a",
        "https://user@push.example.test/a",
        "https://push.example.test.evil.test/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://push.example.test/a#fragment",
        "https://push.example.test/a\n",
        "https://push.example.test\\evil/a",
    ],
)
def test_reject_unsafe_urls(endpoint):
    with pytest.raises(UnsafePushEndpointError):
        validate_endpoint(endpoint, ["push.example.test", "127.0.0.1", "::1"])


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "224.0.0.1", "0.0.0.0"]
)
def test_any_non_public_dns_answer_blocks_request(monkeypatch, address):
    monkeypatch.setattr(
        push_transport.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443)), (0, 0, 0, "", (address, 443))],
    )
    with pytest.raises(UnsafePushEndpointError):
        resolve_endpoint("https://push.example.test/a", ["push.example.test"])


def test_tcp_uses_pinned_address_and_tls_uses_original_host(monkeypatch):
    raw = Mock()
    connect = Mock(return_value=raw)
    monkeypatch.setattr(push_transport.socket, "create_connection", connect)
    connection = PinnedHTTPSConnection("push.example.test", "8.8.8.8", 10)
    context = Mock()
    connection.tls_context = context
    connection.connect()
    connect.assert_called_once_with(("8.8.8.8", 443), timeout=10)
    context.wrap_socket.assert_called_once_with(raw, server_hostname="push.example.test")


def test_no_redirect_proxy_or_provider_body_is_used(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    monkeypatch.setattr(push_transport, "resolve_endpoint", lambda *args: ("push.example.test", "8.8.8.8"))
    connection = Mock()
    connection.getresponse.return_value.status = 307
    connection.getresponse.return_value.getheader.return_value = ""
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(push_transport, "PinnedHTTPSConnection", constructor)
    result = PinnedPushSession(["push.example.test"]).post("https://push.example.test/a?b=c", b"encrypted", {}, 10)
    assert result.status_code == 307 and result.content == b""
    constructor.assert_called_once_with("push.example.test", "8.8.8.8", 10)
    connection.request.assert_called_once_with("POST", "/a?b=c", b"encrypted", {})
    connection.getresponse.return_value.read.assert_not_called()
    connection.close.assert_called_once()


def test_synthetic_vapid_and_payload_encryption_roundtrip(push_settings, subscription_request, monkeypatch):
    receiver = ec.generate_private_key(ec.SECP256R1())
    subscription_request.keys.p256dh = encoded_public(receiver)
    captured = {}

    def capture(self, url, data, headers, timeout):
        captured.update(data=data, headers=headers)
        result = push_transport.Response()
        result.status_code = 201
        result._content = b""
        return result

    monkeypatch.setattr(PinnedPushSession, "post", capture)
    payload = {
        "title": "복약 기록 알림",
        "body": "앱에서 기록을 확인해 주세요.",
        "notification_id": "synthetic-id",
        "generation": "synthetic-generation",
    }
    result = send_push(push_settings, subscription_request.model_dump_json(), payload, 123)
    assert result.status == "ACCEPTED"
    assert captured["headers"]["ttl"] == "123"
    assert captured["headers"]["content-encoding"] == "aes128gcm"
    clear = http_ece.decrypt(
        captured["data"], private_key=receiver, auth_secret=b"synthetic-auth16", version="aes128gcm"
    )
    assert json.loads(clear) == payload
    assert "복약".encode() not in captured["data"]
    auth = captured["headers"]["Authorization"]
    token = auth.split("t=", 1)[1].split(",", 1)[0]
    public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.urlsafe_b64decode(push_settings.vapid_public_key + "=")
    )
    claims = jwt.decode(token, public, algorithms=["ES256"], audience="https://push.example.test")
    assert claims["sub"] == "mailto:synthetic@example.test"


@pytest.mark.parametrize(
    "http_status,expected,reason",
    [
        (201, "ACCEPTED", None),
        (204, "ACCEPTED", None),
        (404, "FAILED", "SUBSCRIPTION_EXPIRED"),
        (410, "FAILED", "SUBSCRIPTION_EXPIRED"),
        (429, "PENDING", "PROVIDER_RETRY"),
        (503, "PENDING", "PROVIDER_RETRY"),
        (403, "FAILED", "PROVIDER_REJECTED"),
        (307, "FAILED", "PROVIDER_REJECTED"),
    ],
)
def test_provider_outcome_classification(
    push_settings, subscription_request, monkeypatch, http_status, expected, reason
):
    def response(*args, **kwargs):
        result = push_transport.Response()
        result.status_code = http_status
        result._content = b""
        result.headers["Retry-After"] = "90"
        return result

    monkeypatch.setattr(PinnedPushSession, "post", response)
    result = send_push(push_settings, subscription_request.model_dump_json(), {}, 100)
    assert (result.status, result.reason) == (expected, reason)
    if expected == "PENDING":
        assert result.retry_after_seconds == 90


def test_timeout_and_sensitive_exception_are_not_exposed(push_settings, subscription_request, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise TimeoutError("SENSITIVE_ENDPOINT_SENTINEL")

    monkeypatch.setattr(PinnedPushSession, "post", fail)
    result = send_push(push_settings, subscription_request.model_dump_json(), {}, 100)
    assert result.status == "UNKNOWN"
    assert "SENSITIVE_ENDPOINT_SENTINEL" not in str(result) + caplog.text


def test_retry_after_http_date_and_invalid_values():
    assert retry_after_seconds("Sun, 13 Sep 2026 04:01:00 GMT", NOW) == 60
    assert retry_after_seconds("garbage", NOW) == 0
    assert retry_after_seconds("99999999", NOW) == 86400
    assert retry_after_seconds("Sun, 13 Sep 2026 04:01:00 GMT", NOW + timedelta(hours=1)) == 0


def test_key_configuration_fails_closed(push_settings):
    with pytest.raises(ValidationError):
        PushSettings.model_validate({**push_settings.model_dump(), "vapid_public_key": "mismatched"})
    assert PushSettings(enabled=False).enabled is False


def test_slow_dns_cannot_start_http_after_application_timeout(monkeypatch):
    monkeypatch.setattr(push_transport, "resolve_endpoint", lambda *args: ("push.example.test", "8.8.8.8"))
    connection = Mock()
    monkeypatch.setattr(push_transport, "PinnedHTTPSConnection", connection)
    with pytest.raises(TimeoutError):
        PinnedPushSession(["push.example.test"], deadline=0).post("https://push.example.test/a", b"", {}, 10)
    connection.assert_not_called()
