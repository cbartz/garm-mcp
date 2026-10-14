import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def require_bearer(expected_token: str, app: ASGIApp) -> ASGIApp:
    expected = f"Bearer {expected_token}".encode()

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await app(scope, receive, send)
            return
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            supplied = headers.get(b"authorization", b"")
            if hmac.compare_digest(supplied, expected):
                await app(scope, receive, send)
                return
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})

    return middleware
