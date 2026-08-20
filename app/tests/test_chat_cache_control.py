import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.chat_cache_control import ChatNoStoreMiddleware, is_chat_api_path


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> None:
    return None


@pytest.fixture(autouse=True)
def isolate_database() -> None:
    return None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/not-a-uuid/messages",
        "/api/v1/chat-sessions/not-a-uuid/messages/",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions/",
    ],
)
def test_is_chat_api_path_accepts_only_supported_route_shapes(path: str) -> None:
    assert is_chat_api_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/id/messages/extra",
        "/api/v1/chat-sessions/id/messages//",
        "//api/v1/chat-sessions/id/messages",
        "/api/v1/prescriptions/id/chat-sessions/extra",
        "/api/v1/guides/id",
        "/health",
    ],
)
def test_is_chat_api_path_rejects_unrelated_or_malformed_paths(path: str) -> None:
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
        "client": None,
        "server": None,
        "root_path": "",
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_middleware_preserves_headers_and_overwrites_cache_control() -> None:
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b"Bearer"), (b"cache-control", b"private")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def send(message: Message) -> None:
        sent.append(message)

    await ChatNoStoreMiddleware(downstream)(_http_scope("/api/v1/chat-sessions/id/messages"), _receive, send)

    headers = dict(sent[0]["headers"])
    assert headers[b"www-authenticate"] == b"Bearer"
    assert headers[b"cache-control"] == b"no-store"


@pytest.mark.asyncio
async def test_middleware_forwards_non_chat_http_scope_without_header_mutation() -> None:
    received: list[tuple[Scope, Message]] = []
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        received.append((scope, await receive()))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"private")],
            }
        )

    async def send(message: Message) -> None:
        sent.append(message)

    scope = _http_scope("/api/v1/guides/id")
    await ChatNoStoreMiddleware(downstream)(scope, _receive, send)

    assert received == [(scope, {"type": "http.request", "body": b"", "more_body": False})]
    assert sent == [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"cache-control", b"private")],
        }
    ]


@pytest.mark.asyncio
async def test_middleware_forwards_websocket_scope_without_header_mutation() -> None:
    received: list[tuple[Scope, Message]] = []
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        received.append((scope, await receive()))
        await send({"type": "websocket.accept", "headers": [(b"x-test", b"value")]})

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": "/api/v1/chat-sessions/id/messages",
        "raw_path": b"/api/v1/chat-sessions/id/messages",
        "query_string": b"",
        "headers": [],
        "client": None,
        "server": None,
        "subprotocols": [],
        "root_path": "",
    }
    await ChatNoStoreMiddleware(downstream)(scope, receive, send)

    assert received == [(scope, {"type": "websocket.connect"})]
    assert sent == [{"type": "websocket.accept", "headers": [(b"x-test", b"value")]}]
