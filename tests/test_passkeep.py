import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient

import simsaas.identity as ident
from simsaas.identity import Passkeep, create_app

REDIRECT = "https://shop.example/callback"


class Clock:
    t = 1_790_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def idp(monkeypatch):
    monkeypatch.setattr(ident, "_hash_password", _fast_hash)
    clock = Clock()
    pk = Passkeep("https://id.passkeep.test", clock=clock)
    pk.add_client("shop-web", [REDIRECT], ["openid", "profile", "orders"])
    pk.add_client("shop-bff", [REDIRECT], ["openid", "orders"], secret="bff-secret", audience="shop-api")
    pk.add_user("ana", "correct horse battery staple", {"tenant": "t1"})
    return pk, TestClient(create_app(pk, "op-admin"), follow_redirects=False), clock


def _fast_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    return base64.b64encode(salt + hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000)).decode()


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize(client, **over):
    verifier, challenge = pkce()
    form = {"response_type": "code", "client_id": "shop-web", "redirect_uri": REDIRECT, "scope": "openid orders",
            "state": "st-1", "nonce": "n-1", "code_challenge": challenge, "code_challenge_method": "S256",
            "username": "ana", "password": "correct horse battery staple", **over}
    r = client.post("/authorize", data={k: v for k, v in form.items() if v is not None})
    return r, verifier


def code_from(r):
    assert r.status_code == 303, r.text
    return parse_qs(urlparse(r.headers["location"]).query)


def login(client):
    r, verifier = authorize(client)
    code = code_from(r)["code"][0]
    t = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                    "client_id": "shop-web", "code_verifier": verifier})
    assert t.status_code == 200, t.text
    return t.json(), code, verifier


def test_full_flow_with_id_token_and_jwks(idp):
    pk, c, clock = idp
    tokens, _, _ = login(c)
    key = jwt.PyJWK(c.get("/jwks").json()["keys"][0]).key
    at = jwt.decode(tokens["access_token"], key, algorithms=["RS256"], audience="shop-web",
                    options={"verify_exp": False})
    assert jwt.get_unverified_header(tokens["access_token"])["typ"] == "at+jwt" and at["tenant"] == "t1"
    idt = jwt.decode(tokens["id_token"], key, algorithms=["RS256"], audience="shop-web", options={"verify_exp": False})
    assert idt["nonce"] == "n-1"
    assert c.get("/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()["tenant"] == "t1"


@pytest.mark.parametrize("over,error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge": None, "code_challenge_method": None}, "invalid_request"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"scope": "openid admin"}, "invalid_scope"),
    ({"password": "wrong"}, "access_denied"),
])
def test_authorize_rejections_redirect_with_error(idp, over, error):
    pk, c, clock = idp
    r, _ = authorize(c, **over)
    q = code_from(r)
    assert q["error"] == [error] and "code" not in q


def test_redirect_uri_must_match_exactly(idp):
    pk, c, clock = idp
    for bad in (REDIRECT + "/x", REDIRECT + "?a=1", "https://evil.example/callback"):
        r, _ = authorize(c, redirect_uri=bad)
        assert r.status_code == 400 and "location" not in r.headers


def test_wrong_verifier_and_password_grant(idp):
    pk, c, clock = idp
    r, _ = authorize(c)
    code = code_from(r)["code"][0]
    bad = c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                 "client_id": "shop-web", "code_verifier": "nope"})
    assert bad.json()["error"] == "invalid_grant"
    pw = c.post("/token", data={"grant_type": "password", "username": "ana", "password": "x", "client_id": "shop-web"})
    assert pw.json()["error"] == "unsupported_grant_type"


def test_code_replay_revokes_issued_tokens(idp):
    pk, c, clock = idp
    tokens, code, verifier = login(c)
    replay = c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                    "client_id": "shop-web", "code_verifier": verifier})
    assert replay.json()["error"] == "invalid_grant"
    assert c.get("/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401
    r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                               "client_id": "shop-web"})
    assert r.json()["error"] == "invalid_grant"


def test_refresh_rotation_and_reuse_detection(idp):
    pk, c, clock = idp
    tokens, _, _ = login(c)
    r1 = tokens["refresh_token"]
    t2 = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": r1, "client_id": "shop-web"}).json()
    r2 = t2["refresh_token"]
    assert r2 != r1
    reuse = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": r1, "client_id": "shop-web"})
    assert reuse.json()["error"] == "invalid_grant"
    dead = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": r2, "client_id": "shop-web"})
    assert dead.json()["error"] == "invalid_grant"  # the whole family is revoked
    assert c.get("/userinfo", headers={"Authorization": f"Bearer {t2['access_token']}"}).status_code == 401
    events = c.get("/admin/events", headers={"Authorization": "Bearer op-admin"}).json()["events"]
    assert any(e["event"] == "refresh_reuse_detected" for e in events)


def test_access_token_expires(idp):
    pk, c, clock = idp
    tokens, _, _ = login(c)
    clock.t += ident.ACCESS_TTL
    assert c.get("/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401


def test_confidential_client_auth_revoke_and_introspect(idp):
    pk, c, clock = idp
    auth = "Basic " + base64.b64encode(b"shop-bff:bff-secret").decode()
    wrong = "Basic " + base64.b64encode(b"shop-bff:nope").decode()
    assert c.post("/token", data={"grant_type": "client_credentials"}, headers={"Authorization": wrong}).status_code == 401
    t = c.post("/token", data={"grant_type": "client_credentials", "scope": "orders"},
               headers={"Authorization": auth}).json()
    assert "refresh_token" not in t
    assert pk.verify_access(t["access_token"], "shop-api")["sub"] == "client:shop-bff"
    assert c.post("/introspect", data={"token": t["access_token"]}, headers={"Authorization": auth}).json()["active"]
    c.post("/revoke", data={"token": t["access_token"]}, headers={"Authorization": auth})
    assert not c.post("/introspect", data={"token": t["access_token"]}, headers={"Authorization": auth}).json()["active"]
    assert c.post("/token", data={"grant_type": "client_credentials", "client_id": "shop-web"}).json()["error"] == \
        "unauthorized_client"


def test_logout_revokes_refresh_family(idp):
    pk, c, clock = idp
    tokens, _, _ = login(c)
    assert c.post("/revoke", data={"token": tokens["refresh_token"], "client_id": "shop-web"}).status_code == 200
    r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                               "client_id": "shop-web"})
    assert r.json()["error"] == "invalid_grant"
    assert c.get("/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401


def test_events_are_operator_only(idp):
    pk, c, clock = idp
    assert c.get("/admin/events").status_code == 403


def test_claims_template_renames_claims_per_client():
    from simsaas.identity import Client, Passkeep
    legacy = Client("legacy", ["http://x/cb"], ["openid"], claims_template={"org": "tenant", "email": "email"})
    modern = Client("modern", ["http://x/cb"], ["openid"])
    user = {"sub": "usr_1", "claims": {"tenant": "acme", "email": "a@x", "role": "admin"}}
    assert Passkeep.claims_for(legacy, user) == {"org": "acme", "email": "a@x"}
    assert Passkeep.claims_for(modern, user) == user["claims"]
