from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_chat_api_path(path: str) -> bool:
    segments = path.split("/")
    if segments[-1] == "":
        segments = segments[:-1]

    if len(segments) != 6 or not segments[4]:
        return False

    return (segments[:4] == ["", "api", "v1", "chat-sessions"] and segments[5] == "messages") or (
        segments[:4] == ["", "api", "v1", "prescriptions"] and segments[5] == "chat-sessions"
    )


class ChatNoStoreMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_chat_api_path(scope["path"]):
            await self._app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = [
                    (name, value) for name, value in message.get("headers", []) if name.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_no_store)
