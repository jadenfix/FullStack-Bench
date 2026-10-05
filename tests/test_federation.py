import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from conftest import auth

ISSUER = "https://forge.internal"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_PEM = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
TRUST = {"issuer": ISSUER, "audience": "simcloud", "subject": "repo:shop/api:ref:refs/heads/*",
         "claims": {"repository": "shop/api"}, "service_account": "deployer", "max_ttl_seconds": 900}


@pytest.fixture
def fed(client, admin, clock):
    jwk = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
    assert client.post("/admin/v1/issuers", json={"issuer": ISSUER, "jwks": {"keys": [{**jwk, "kid": "ci-1"}]}},
                       headers=admin).status_code == 201
    for kind, name, spec in [
        ("service_account", "deployer", {}),
        ("trust", "ci-main", TRUST),
        ("policy", "deploy-prod", {"statements": [{
            "effect": "allow", "actions": ["kv:create", "kv:update"], "resources": ["srn:simcloud:shop:prod:kv/*"],
            "conditions": {"string_equals": {"claims.ref": "refs/heads/main"}}}]}),
        ("binding", "deployer-b", {"principal": "service-account:deployer", "policies": ["deploy-prod"]}),
    ]:
        r = client.put(f"/v1/projects/shop/envs/_/{kind}/{name}", json={"spec": spec}, headers=admin)
        assert r.status_code == 200, r.text
    return clock


def ci_token(clock, *, key=KEY, alg="RS256", kid="ci-1", **overrides):
    now = int(clock.now())
    claims = {"iss": ISSUER, "aud": "simcloud", "sub": "repo:shop/api:ref:refs/heads/main", "iat": now,
              "nbf": now, "exp": now + 300, "repository": "shop/api", "ref": "refs/heads/main", **overrides}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def exchange(client, token, **kw):
    return client.post("/v1/projects/shop/federation/token", json={"subject_token": token, **kw})


def test_exchange_then_use_short_lived_token(client, fed):
    r = exchange(client, ci_token(fed))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal"] == "service-account:deployer" and body["expires_in"] == 900
    h = auth(body["access_token"])
    assert client.put("/v1/projects/shop/envs/prod/kv/flags", json={"spec": {}}, headers=h).status_code == 200
    who = client.get("/v1/whoami", headers=h).json()
    assert who["claims"]["ref"] == "refs/heads/main" and who["claims"]["trust"] == "ci-main"
    fed.advance(900)
    assert client.get("/v1/whoami", headers=h).status_code == 401


def test_branch_token_exchanges_but_policy_condition_blocks_prod(client, fed):
    tok = ci_token(fed, sub="repo:shop/api:ref:refs/heads/feature", ref="refs/heads/feature")
    h = auth(exchange(client, tok).json()["access_token"])
    assert client.put("/v1/projects/shop/envs/prod/kv/flags", json={"spec": {}}, headers=h).status_code == 403


def test_ttl_is_capped(client, fed):
    assert exchange(client, ci_token(fed), ttl_seconds=3600).json()["expires_in"] == 900
    assert exchange(client, ci_token(fed), ttl_seconds=120).json()["expires_in"] == 120


@pytest.mark.parametrize("case", [
    "alg_none", "hs256_with_public_key", "wrong_key", "unknown_kid", "wrong_aud", "untrusted_issuer",
    "expired", "not_yet_valid", "subject_mismatch", "claim_mismatch", "missing_exp",
])
def test_rfc8725_rejections(client, fed, case):
    now = int(fed.now())
    tokens = {
        "alg_none": lambda: jwt.encode({"iss": ISSUER, "aud": "simcloud", "sub": "repo:shop/api:ref:refs/heads/main",
                                        "iat": now, "exp": now + 300, "repository": "shop/api"}, None,
                                       algorithm="none", headers={"kid": "ci-1"}),
        "hs256_with_public_key": lambda: _hs256_confusion(now),
        "wrong_key": lambda: ci_token(fed, key=OTHER_KEY),
        "unknown_kid": lambda: ci_token(fed, kid="ci-9"),
        "wrong_aud": lambda: ci_token(fed, aud="someone-else"),
        "untrusted_issuer": lambda: ci_token(fed, iss="https://evil.example"),
        "expired": lambda: ci_token(fed, exp=now - 120, iat=now - 600, nbf=now - 600),
        "not_yet_valid": lambda: ci_token(fed, nbf=now + 600),
        "subject_mismatch": lambda: ci_token(fed, sub="repo:shop/other:ref:refs/heads/main"),
        "claim_mismatch": lambda: ci_token(fed, repository="shop/other"),
        "missing_exp": lambda: ci_token(fed, exp=None),
    }
    r = exchange(client, tokens[case]())
    assert r.status_code in (401, 403), (case, r.text)


def _hs256_confusion(now):
    # Classic algorithm confusion: sign with HMAC using the RSA public key as the secret.
    import base64, hashlib, hmac
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    head, body = b64({"alg": "HS256", "kid": "ci-1", "typ": "JWT"}), b64({
        "iss": ISSUER, "aud": "simcloud", "sub": "repo:shop/api:ref:refs/heads/main", "iat": now,
        "exp": now + 300, "repository": "shop/api"})
    sig = base64.urlsafe_b64encode(hmac.new(PUB_PEM, head + b"." + body, hashlib.sha256).digest()).rstrip(b"=")
    return (head + b"." + body + b"." + sig).decode()


def test_denied_exchange_is_audited_without_token(client, fed, dev):
    exchange(client, ci_token(fed, repository="shop/other"))
    items = client.get("/v1/projects/shop/audit", headers=dev).json()["items"]
    denied = [i for i in items if i["action"] == "federation:exchange" and i["outcome"] == "denied"]
    assert denied and "eyJ" not in json.dumps(denied)


def test_long_lived_key_needs_permission_and_is_listed(client, fed, dev, admin):
    assert client.post("/v1/projects/shop/service-accounts/deployer/keys", headers=dev).status_code == 403
    r = client.post("/v1/projects/shop/service-accounts/deployer/keys", headers=admin)
    assert r.status_code == 201 and r.json()["expires_at"] is None
    tokens = client.get("/v1/projects/shop/tokens", headers=admin).json()["items"]
    assert any(t["kind"] == "sa_key" and t["principal"] == "service-account:deployer" for t in tokens)
    key_id = r.json()["id"]
    assert client.delete(f"/v1/projects/shop/tokens/{key_id}", headers=admin).status_code == 204
    assert client.get("/v1/whoami", headers=auth(r.json()["key"])).status_code == 401


def test_workload_id_token_verifies_against_jwks(client, fed, admin):
    r = client.post("/v1/projects/shop/service-accounts/deployer/id-token", json={"audience": "billing-api"},
                    headers=admin)
    token = r.json()["id_token"]
    jwks = client.get("/v1/oidc/jwks").json()
    key = ECAlgorithm.from_jwk(json.dumps(jwks["keys"][0]))
    claims = jwt.decode(token, key, algorithms=["ES256"], audience="billing-api",
                        options={"verify_exp": False, "verify_iat": False, "verify_nbf": False})
    assert claims["sub"] == "service-account:shop/deployer"
    assert client.get("/.well-known/openid-configuration").json()["jwks_uri"] == "/v1/oidc/jwks"
