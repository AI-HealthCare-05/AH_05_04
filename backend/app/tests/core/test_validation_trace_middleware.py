import json
from uuid import UUID

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Env
from app.core.validation_trace_middleware import ValidationTraceMiddleware


def _scope(*, validation_run_id: str | None = None) -> Scope:
    headers = [] if validation_run_id is None else [(b"x-validation-run-id", validation_run_id.encode("ascii"))]
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/synthetic",
        "raw_path": b"/api/v1/synthetic",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _call(
    *, validation_run_id: str | None, environment: Env = Env.LOCAL, validation_enabled: bool = True
) -> tuple[list[Message], Scope]:
    sent: list[Message] = []
    scope = _scope(validation_run_id=validation_run_id)

    async def downstream(inner_scope: Scope, _receive: Receive, send: Send) -> None:
        context = inner_scope["state"]["provider_call_context"]
        assert context.trace_id == inner_scope["state"]["trace_id"]
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def capture(message: Message) -> None:
        sent.append(message)

    middleware = ValidationTraceMiddleware(
        downstream,
        environment=environment,
        validation_enabled=validation_enabled,
    )
    await middleware(scope, _receive, capture)
    return sent, scope


async def test_valid_validation_run_id_builds_context_and_adds_trace_header() -> None:
    run_id = "61a10000-0000-4000-8000-000000000003"

    sent, scope = await _call(validation_run_id=run_id)

    trace_id = scope["state"]["trace_id"]
    context = scope["state"]["provider_call_context"]
    assert len(trace_id) == 32
    int(trace_id, 16)
    assert context.validation_run_id == UUID(run_id)
    assert context.environment is Env.LOCAL
    assert context.validation_enabled is True
    assert (b"x-trace-id", trace_id.encode("ascii")) in sent[0]["headers"]


@pytest.mark.parametrize(
    ("validation_run_id", "environment", "validation_enabled", "expected_status", "expected_message"),
    [
        ("not-a-uuid", Env.LOCAL, True, 400, "Invalid validation run ID."),
        ("61a10000-0000-4000-8000-000000000003", Env.LOCAL, False, 403, "Validation run is not allowed."),
        ("61a10000-0000-4000-8000-000000000003", Env.STAGING, True, 403, "Validation run is not allowed."),
    ],
)
async def test_invalid_or_unapproved_validation_header_fails_fast_with_safe_error(
    validation_run_id: str,
    environment: Env,
    validation_enabled: bool,
    expected_status: int,
    expected_message: str,
) -> None:
    sent, scope = await _call(
        validation_run_id=validation_run_id,
        environment=environment,
        validation_enabled=validation_enabled,
    )

    assert sent[0]["status"] == expected_status
    headers = dict(sent[0]["headers"])
    assert headers[b"x-trace-id"] == scope["state"]["trace_id"].encode("ascii")
    body = json.loads(sent[1]["body"])
    assert body == {
        "code": "HTTP_ERROR",
        "message": expected_message,
        "details": [],
        "trace_id": scope["state"]["trace_id"],
    }


async def test_request_without_validation_header_remains_a_general_request() -> None:
    sent, scope = await _call(validation_run_id=None, validation_enabled=False)

    assert sent[0]["status"] == 204
    assert scope["state"]["provider_call_context"].validation_run_id is None
