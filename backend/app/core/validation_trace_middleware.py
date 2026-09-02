import uuid
from collections.abc import Awaitable, Callable

from fastapi.responses import ORJSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Env
from app.core.provider_observability import ProviderCallContext

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestTraceMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        trace_id = uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["trace_id"] = trace_id

        async def add_trace_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Trace-Id"] = trace_id
            await send(message)

        await self._app(scope, receive, add_trace_header)


class ValidationTraceMiddleware:
    def __init__(self, app: ASGIApp, *, environment: Env, validation_enabled: bool) -> None:
        self._app = app
        self._environment = environment
        self._validation_enabled = validation_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        trace_id = state.get("trace_id")
        if not isinstance(trace_id, str):
            trace_id = uuid.uuid4().hex
        state["trace_id"] = trace_id
        accepted, validation_run_id = await self._validation_run_id(scope, trace_id, receive, send)
        if not accepted:
            return
        state["provider_call_context"] = ProviderCallContext(
            trace_id=trace_id,
            validation_run_id=validation_run_id,
            environment=self._environment,
            validation_enabled=self._validation_enabled,
        )

        async def add_trace_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Trace-Id"] = trace_id
            await send(message)

        await self._app(scope, receive, add_trace_header)

    async def _validation_run_id(
        self,
        scope: Scope,
        trace_id: str,
        receive: Receive,
        send: Send,
    ) -> tuple[bool, uuid.UUID | None]:
        raw_run_id = Headers(scope=scope).get("X-Validation-Run-Id")
        if raw_run_id is None:
            return True, None
        if not self._validation_enabled or self._environment is not Env.LOCAL:
            await self._send_error(
                trace_id=trace_id,
                status_code=403,
                message="Validation run is not allowed.",
                scope=scope,
                receive=receive,
                send=send,
            )
            return False, None
        try:
            return True, uuid.UUID(raw_run_id)
        except ValueError:
            await self._send_error(
                trace_id=trace_id,
                status_code=400,
                message="Invalid validation run ID.",
                scope=scope,
                receive=receive,
                send=send,
            )
            return False, None

    @staticmethod
    async def _send_error(
        *,
        trace_id: str,
        status_code: int,
        message: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = ORJSONResponse(
            status_code=status_code,
            content={
                "code": "HTTP_ERROR",
                "message": message,
                "details": [],
                "trace_id": trace_id,
            },
            headers={"X-Trace-Id": trace_id},
        )
        await response(scope, receive, send)
