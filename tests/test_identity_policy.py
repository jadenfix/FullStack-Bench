import pytest

from simcloud.clock import FakeClock
from simcloud.errors import SimCloudError
from simcloud.identity import Principal, Tokens
from simcloud.kinds import all_actions, validate_spec
from simcloud.policy import PolicyEngine, matches
from simcloud.store import Store, srn


@pytest.fixture
def env():
    clock = FakeClock()
    store = Store(":memory:", clock)
    return clock, store, Tokens(store, clock, admin_token="verifier-secret"), PolicyEngine(store, clock)


# ---- tokens ----------------------------------------------------------------

def test_token_roundtrip_and_hash_only_storage(env):
    _, store, tokens, _ = env
    token, record = tokens.issue("user:dev", "shop")
    assert "digest" not in record
    stored = store.kv_get("tokens", record["id"])
    assert token.split("_", 2)[2] not in str(stored)
    assert tokens.authenticate(token) == Principal("user:dev", "shop", {}, record["id"])


def test_token_expiry_and_revocation(env):
    clock, _, tokens, _ = env
    token, record = tokens.issue("federated:ci", "shop", ttl_seconds=900)
    clock.advance(899)
    tokens.authenticate(token)
    clock.advance(1)
    with pytest.raises(SimCloudError, match="expired"):
        tokens.authenticate(token)
    token2, record2 = tokens.issue("user:dev", "shop")
    tokens.revoke(record2["id"])
    with pytest.raises(SimCloudError, match="revoked"):
        tokens.authenticate(token2)


@pytest.mark.parametrize("bad", [None, "", "garbage", "sct_nope_x", "sct_a_b_c"])
def test_bad_tokens_rejected(env, bad):
    with pytest.raises(SimCloudError) as e:
        env[2].authenticate(bad)
    assert e.value.code == "unauthenticated"


def test_admin_token_is_not_listed(env):
    _, _, tokens, _ = env
    assert tokens.authenticate("verifier-secret").is_admin
    tokens.issue("user:dev", "shop")
    assert all(t["principal"] != "admin" for t in tokens.list_for_project("shop"))


# ---- policy evaluation -------------------------------------------------------

PROD_API = srn("shop", "prod", "service", "api")
DEV = Principal("user:dev", "shop")


def grant(engine, name, statements, principal="user:dev"):
    engine.record("shop", "policy", name, {"statements": statements})
    engine.record("shop", "binding", f"{name}-b", {"principal": principal, "policies": [name]})


def test_glob_matching():
    assert matches("srn:simcloud:shop:*:service/*", PROD_API)
    assert matches("service:*", "service:deploy")
    assert not matches("service:de?", "service:deploy")
    assert matches("a.b", "a.b") and not matches("a.b", "aXb")


def test_deny_by_default_and_allow(env):
    *_, engine = env
    assert not engine.evaluate(DEV, "service:deploy", PROD_API, "shop").allowed
    grant(engine, "deployer", [{"effect": "allow", "actions": ["service:deploy"], "resources": [PROD_API]}])
    d = engine.evaluate(DEV, "service:deploy", PROD_API, "shop")
    assert d.allowed and d.policy == "deployer"
    assert not engine.evaluate(DEV, "service:delete", PROD_API, "shop").allowed


def test_explicit_deny_wins(env):
    *_, engine = env
    grant(engine, "all", [{"effect": "allow", "actions": ["*"], "resources": ["*"]}])
    grant(engine, "no-prod", [{"effect": "deny", "actions": ["*"], "resources": ["srn:simcloud:shop:prod:*"]}])
    assert not engine.evaluate(DEV, "service:read", PROD_API, "shop").allowed
    assert engine.evaluate(DEV, "service:read", srn("shop", "dev", "service", "api"), "shop").allowed


def test_cross_project_principal_denied(env):
    *_, engine = env
    grant(engine, "all", [{"effect": "allow", "actions": ["*"], "resources": ["*"]}])
    other = Principal("user:dev", "other-project")
    assert not engine.evaluate(other, "service:read", PROD_API, "shop").allowed


def test_conditions_on_claims(env):
    *_, engine = env
    ci = Principal("federated:ci", "shop", {"ref": "refs/heads/main"})
    grant(engine, "ci", [{
        "effect": "allow", "actions": ["service:deploy"], "resources": [PROD_API],
        "conditions": {"string_equals": {"claims.ref": "refs/heads/main"}},
    }], principal="federated:ci")
    assert engine.evaluate(ci, "service:deploy", PROD_API, "shop").allowed
    branch = Principal("federated:ci", "shop", {"ref": "refs/heads/feature"})
    assert not engine.evaluate(branch, "service:deploy", PROD_API, "shop").allowed
    no_claim = Principal("federated:ci", "shop")
    assert not engine.evaluate(no_claim, "service:deploy", PROD_API, "shop").allowed


def test_propagation_delay(env):
    clock, store, _, _ = env
    delay = {"s": 30.0}
    engine = PolicyEngine(store, clock, lambda: delay["s"])
    grant(engine, "deployer", [{"effect": "allow", "actions": ["service:deploy"], "resources": [PROD_API]}])
    assert not engine.evaluate(DEV, "service:deploy", PROD_API, "shop").allowed
    clock.advance(30)
    assert engine.evaluate(DEV, "service:deploy", PROD_API, "shop").allowed
    # A revocation also takes time to propagate: the old grant still works until then.
    engine.record("shop", "binding", "deployer-b", None)
    assert engine.evaluate(DEV, "service:deploy", PROD_API, "shop").allowed
    clock.advance(30)
    assert not engine.evaluate(DEV, "service:deploy", PROD_API, "shop").allowed


def test_effective_permissions_for_least_privilege(env):
    *_, engine = env
    grant(engine, "reader", [{"effect": "allow", "actions": ["service:read", "service:logs"], "resources": [PROD_API]}])
    perms = engine.effective_permissions(DEV, "shop", [PROD_API])
    assert perms == [("service:read", PROD_API), ("service:logs", PROD_API)]


def test_action_catalogue_and_spec_defaults():
    assert "secret:access" in all_actions() and "service:deploy" in all_actions()
    spec = validate_spec("queue", {"max_receives": 5, "dead_letter_queue": "orders-dlq"})
    assert spec["visibility_timeout_seconds"] == 30
    with pytest.raises(Exception):
        validate_spec("queue", {"unknown_field": 1})
