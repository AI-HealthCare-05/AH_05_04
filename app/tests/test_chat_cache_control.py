import pytest
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Message, Receive, Scope, Send

from app.core.chat_cache_control import ChatNoStoreMiddleware, is_chat_api_path
from app.main import app as application
from app.main import fastapi_app


@pytest.fixture(scope="session")
def initialize_database() -> None:
    """Keep this pure ASGI unit suite independent from the MySQL fixture."""


@pytest.fixture
def isolate_database() -> None:
    """Keep this pure ASGI unit suite independent from the MySQL fixture."""


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/session-id/messages",
        "/api/v1/chat-sessions/session-id/messages/",
        "/api/v1/chat-sessions/not-a-uuid/messages",
        "/api/v1/prescriptions/prescription-id/chat-sessions",
        "/api/v1/prescriptions/prescription-id/chat-sessions/",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions",
    ],
)
def test_is_chat_api_path_accepts_only_supported_route_shapes(path: str) -> None:
    assert is_chat_api_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions//messages",
        "/api/v1/chat-sessions/session-id/messages//",
        "/api/v1/chat-sessions/session-id/messages/extra",
        "/api/v1/chat-sessions-prefix/session-id/messages",
        "/prefix/api/v1/chat-sessions/session-id/messages",
        "/api/v1/prescriptions//chat-sessions",
        "/api/v1/prescriptions/prescription-id/chat-sessions//",
        "/api/v1/prescriptions/prescription-id/chat-sessions/extra",
        "/api/v1/prescriptions-prefix/prescription-id/chat-sessions",
        "/api/v1/prescriptions/prescription-id",
        "/api/v1/guides/guide-id",
        "/api/v1/chat-sessions/session-id/messages?limit=20",
    ],
)
def test_is_chat_api_path_rejects_partial_additional_and_unrelated_paths(path: str) -> None:
    assert not is_chat_api_path(path)


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


async def test_middleware_preserves_headers_and_replaces_all_cache_control_values() -> None:
    sent: list[Message] = []
    response_start: Message = {
        "type": "http.response.start",
        "status": 401,
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

    middleware = ChatNoStoreMiddleware(downstream)
    await middleware(
        _http_scope("/api/v1/chat-sessions/not-a-uuid/messages"),
        _receive_http_request,
        capture,
    )

    assert sent[1] is response_body
    assert sent[0]["status"] == 401
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
            _http_scope("/api/v1/guides/guide-id"),
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

    middleware = ChatNoStoreMiddleware(downstream)
    await middleware(scope, receive, capture)

    assert received_scope is scope
    assert received_receive is receive
    assert received_send is capture
    assert sent == [first_message]
    assert sent[0] is first_message


def test_application_wraps_chat_cache_control_inside_outer_cors() -> None:
    assert isinstance(application, CORSMiddleware)
    assert isinstance(application.app, ChatNoStoreMiddleware)
    assert application.app._app is fastapi_app
