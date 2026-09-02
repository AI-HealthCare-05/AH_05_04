import pytest
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Message, Receive, Scope, Send

from app.core.no_store_middleware import NoStoreMiddleware, is_api_v1_path
from app.core.validation_trace_middleware import RequestTraceMiddleware, ValidationTraceMiddleware
from app.main import app as application
from app.main import cors_app, fastapi_app


@pytest.fixture(scope="session")
def initialize_database() -> None:
    """Keep this pure ASGI unit suite independent from the MySQL fixture."""


@pytest.fixture
def isolate_database() -> None:
    """Keep this pure ASGI unit suite independent from the MySQL fixture."""


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1",
        "/api/v1/auth/login",
        "/api/v1/auth/token/refresh",
        "/api/v1/users/me",
        "/api/v1/documents",
        "/api/v1/documents/document-id/ocr-jobs",
        "/api/v1/ocr-jobs/job-id",
        "/api/v1/extracted-fields/field-id",
        "/api/v1/prescriptions/prescription-id",
        "/api/v1/guides/guide-id",
        "/api/v1/chat-sessions/session-id/messages",
        "/api/v1/chat-sessions/session-id/messages/",
        "/api/v1/prescriptions/prescription-id/chat-sessions",
    ],
)
def test_is_api_v1_path_accepts_every_v1_route(path: str) -> None:
    assert is_api_v1_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api",
        "/api/v2/users/me",
        "/api/docs",
        "/api/openapi.json",
        "/prefix/api/v1/users/me",
        "/api/v1prefix/users/me",
    ],
)
def test_is_api_v1_path_rejects_non_v1_and_prefix_lookalikes(path: str) -> None:
    assert not is_api_v1_path(path)


def _http_scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": "",
    }


async def _receive_http_request() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login",
        "/api/v1/users/me",
        "/api/v1/chat-sessions/not-a-uuid/messages",
    ],
)
@pytest.mark.parametrize("status_code", [200, 401, 422, 500])
async def test_middleware_preserves_headers_and_replaces_all_cache_control_values(
    path: str,
    status_code: int,
) -> None:
    sent: list[Message] = []
    response_start: Message = {
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"www-authenticate", b"Bearer"),
            (b"access-control-allow-origin", b"https://synthetic.example"),
            (b"set-cookie", b"synthetic-a=1"),
            (b"set-cookie", b"synthetic-b=2"),
            (b"cache-control", b"private"),
            (b"Cache-Control", b"max-age=60"),
        ],
    }
    response_body: Message = {"type": "http.response.body", "body": b"synthetic body"}

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        assert await receive() == {"type": "http.request", "body": b"", "more_body": False}
        await send(response_start)
        await send(response_body)

    async def capture(message: Message) -> None:
        sent.append(message)

    middleware = NoStoreMiddleware(downstream)
    await middleware(_http_scope(path), _receive_http_request, capture)

    assert sent[1] is response_body
    assert sent[0]["status"] == status_code
    headers = sent[0]["headers"]
    assert (b"www-authenticate", b"Bearer") in headers
    assert (b"access-control-allow-origin", b"https://synthetic.example") in headers
    assert headers.count((b"set-cookie", b"synthetic-a=1")) == 1
    assert headers.count((b"set-cookie", b"synthetic-b=2")) == 1
    cache_headers = [(name.lower(), value) for name, value in headers if name.lower() == b"cache-control"]
    assert cache_headers == [(b"cache-control", b"no-store")]


@pytest.mark.parametrize(
    ("scope", "first_message"),
    [
        (
            _http_scope("/api/docs"),
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"public, max-age=60")],
            },
        ),
        (
            {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "scheme": "ws",
                "path": "/api/v1/chat-sessions/session-id/messages",
                "raw_path": b"/api/v1/chat-sessions/session-id/messages",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
                "subprotocols": [],
                "root_path": "",
            },
            {"type": "websocket.accept", "subprotocol": None, "headers": []},
        ),
    ],
)
async def test_middleware_passes_non_target_and_non_http_scopes_through_unchanged(
    scope: Scope,
    first_message: Message,
) -> None:
    sent: list[Message] = []
    received_scope: Scope | None = None
    received_receive: Receive | None = None
    received_send: Send | None = None

    async def receive() -> Message:
        return {"type": "websocket.connect"} if scope["type"] == "websocket" else await _receive_http_request()

    async def downstream(passed_scope: Scope, passed_receive: Receive, passed_send: Send) -> None:
        nonlocal received_scope, received_receive, received_send
        received_scope = passed_scope
        received_receive = passed_receive
        received_send = passed_send
        await passed_send(first_message)

    async def capture(message: Message) -> None:
        sent.append(message)

    middleware = NoStoreMiddleware(downstream)
    await middleware(scope, receive, capture)

    assert received_scope is scope
    assert received_receive is receive
    assert received_send is capture
    assert sent == [first_message]
    assert sent[0] is first_message


def test_application_wraps_cors_and_no_store_inside_outer_trace_boundary() -> None:
    assert isinstance(application, RequestTraceMiddleware)
    assert application._app is cors_app
    assert isinstance(cors_app, CORSMiddleware)
    assert isinstance(cors_app.app, NoStoreMiddleware)
    assert isinstance(cors_app.app._app, ValidationTraceMiddleware)
    assert cors_app.app._app._app is fastapi_app
