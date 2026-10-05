from fastapi.testclient import TestClient

from simsaas.server import build

SEED = {
    "identity": {"port": 7601, "issuer": "http://simsaas:7601",
                 "clients": [{"client_id": "web", "redirect_uris": ["https://x/cb"], "scopes": ["openid"]}],
                 "users": [{"username": "ana", "password": "pw-long-enough"}]},
    "payments": {"port": 7701, "accounts": [{"account": "shop", "api_key": "tp_k"}],
                 "endpoints": [{"account": "shop", "url": "http://127.0.0.1:9/hook"}], "faults": {"reorder": True}},
}


def test_build_both_providers():
    apps = build(SEED, "op")
    assert [port for _, port in apps] == [7601, 7701]
    idp, pay = (TestClient(a) for a, _ in apps)
    assert idp.get("/.well-known/openid-configuration").json()["issuer"] == "http://simsaas:7601"
    assert pay.post("/v1/charges", json={"amount": 5, "currency": "usd"},
                    headers={"Authorization": "Bearer tp_k"}).status_code == 201


def test_seed_with_no_providers_builds_nothing():
    assert build({}, "op") == []
