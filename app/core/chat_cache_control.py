from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_chat_api_path(path: str) -> bool:
    segments = path.split("/")
    if segments and segments[-1] == "":
        segments = segments[:-1]
    message_path = (
        len(segments) == 6
        and segments[:4] == ["", "api", "v1", "chat-sessions"]
        and bool(segments[4])
        and segments[5] == "messages"
    )
    session_path = (
        len(segments) == 6
        and segments[:4] == ["", "api", "v1", "prescriptions"]
        and bool(segments[4])
        and segments[5] == "chat-sessions"
    )
    return message_path or session_path


class ChatNoStoreMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_chat_api_path(scope["path"]):
            await self._app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self._app(scope, receive, send_no_store)
