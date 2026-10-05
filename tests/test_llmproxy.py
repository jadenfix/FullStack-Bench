import httpx
import pytest

from simcloud.llmproxy import Proxy

SEEN = []


def upstream(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    if request.url.path == "/v1/chat/completions":
        body = b'{"choices": [{"message": {"content": "hi"}}]}'
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=httpx.ByteStream(body))
    return httpx.Response(404)


@pytest.fixture
def client():
    SEEN.clear()
    logs = []
    proxy = Proxy("https://models.example.com", transport=httpx.MockTransport(upstream), log=logs.append)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://llm-proxy")
    return c, logs


async def test_forwards_model_api_with_auth(client):
    c, logs = client
    r = await c.post("/v1/chat/completions?x=1", json={"model": "m"}, headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hi"
    req = SEEN[0]
    assert str(req.url) == "https://models.example.com/v1/chat/completions?x=1"
    assert req.headers["authorization"] == "Bearer sk-test" and req.headers["host"] == "models.example.com"
    assert logs[-1]["status"] == 200 and "sk-test" not in str(logs)


async def test_refuses_everything_else(client):
    c, logs = client
    for path in ("/simple/requests/", "/github.com/x", "/"):
        r = await c.get(path)
        assert r.status_code == 403
    assert SEEN == [] and logs[-1]["event"] == "refused"


def test_upstream_must_be_a_bare_origin():
    for bad in ("ftp://x", "https://x/v1", "not a url"):
        with pytest.raises(ValueError):
            Proxy(bad)
