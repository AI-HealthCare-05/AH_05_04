from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_api_v1_path(path: str) -> bool:
    segments = path.split("/")
    if segments and segments[-1] == "":
        segments = segments[:-1]
    return len(segments) >= 3 and segments[:3] == ["", "api", "v1"]


class NoStoreMiddleware:
    """`/api/v1/*` 전체 응답(성공·오류 모두)에 Cache-Control: no-store를 적용합니다.

    인증정보 또는 의료·개인정보만 다루고 캐싱이 필요한 엔드포인트가 없으므로
    Chat 전용이던 이전 범위를 API 전체로 일반화합니다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_api_v1_path(scope["path"]):
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
