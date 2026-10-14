from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from garm_mcp.auth import require_bearer


def make_app():
    async def endpoint(request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/tool", endpoint, methods=["GET"])])
    return require_bearer("s3cret", inner)


def test_missing_token_401():
    c = TestClient(make_app())
    r = c.get("/tool")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


def test_wrong_token_401():
    c = TestClient(make_app())
    assert c.get("/tool", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_correct_token_passes_through():
    c = TestClient(make_app())
    assert c.get("/tool", headers={"Authorization": "Bearer s3cret"}).status_code == 200
