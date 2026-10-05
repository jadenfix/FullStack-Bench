# shared helpers. if you're adding something here, consider whether it belongs somewhere else (it probably does)
import hashlib
import hmac
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from functools import wraps

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONF = os.path.join(_HERE, "conf")
_lock = threading.RLock()
_settings_cache = None


def _deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read(name):
    p = os.path.join(_CONF, name)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _env_overrides(prefix="SHOP__"):
    # SHOP__PAYMENTS__PROVIDER=foo -> {"payments": {"provider": "foo"}}
    out = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        parts = k[len(prefix):].lower().split("__")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        try:
            cur[parts[-1]] = json.loads(v)
        except ValueError:
            cur[parts[-1]] = v
    return out


def settings(reload=False):
    global _settings_cache
    with _lock:
        if _settings_cache is None or reload:
            env = os.environ.get("SIMCLOUD_ENV") or os.environ.get("SHOP_ENV") or "dev"
            merged = _deep_merge(_read("app.json"), _read("app.%s.json" % env))
            merged = _deep_merge(merged, _env_overrides())
            merged["_env"] = env
            _settings_cache = merged
        return _settings_cache


def conf(path, default=None):
    cur = settings()
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def envname(*parts):
    return "_".join(str(p) for p in parts if p).upper().replace("-", "_").replace(".", "_")


def env_first(*names, default=None):
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return default


# --- money -------------------------------------------------------------------------------
# NOTE: to_cents() is the old float path, still used by /v0/price. use cents() for anything new.

def to_cents(amount):
    return int(round(float(amount) * 100))


def cents(value, mode="half_even"):
    rounding = ROUND_HALF_EVEN if mode == "half_even" else ROUND_HALF_UP
    return int(Decimal(value).quantize(Decimal("1"), rounding=rounding))


def fmt_money(c, currency="USD"):
    return "%s %d.%02d" % (currency, c // 100, c % 100)


# --- retries -------------------------------------------------------------------------------

def retry(times=3, base=0.05, on=(Exception,)):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            last = None
            for i in range(times):
                try:
                    return fn(*a, **kw)
                except on as e:  # noqa
                    last = e
                    time.sleep(base * (2 ** i) * random.random())
            raise last
        return inner
    return deco


# --- signatures ------------------------------------------------------------------------------

def sign(key, payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


# --- remote toggles (moved off conf/flags.json in Q3) ------------------------------------------

class ToggleError(RuntimeError):
    pass


_toggle_cache = {}


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    pass


def _platform_get(path):
    base = os.environ.get("SIMCLOUD_URL", "").rstrip("/")
    tok = os.environ.get("SIMCLOUD_TOKEN", "")
    if not base:
        raise ToggleError("platform url not configured")
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ToggleError("toggle lookup failed: %s %s" % (e.code, e.read()[:200]))
    except (urllib.error.URLError, OSError) as e:
        raise ToggleError("toggle lookup failed: %s" % e)


def _remote_toggle(name, default=None):
    ns = conf("toggles.namespace", "checkout")
    store = conf("toggles.store", "flags")
    ttl = conf("toggles.cache_seconds", 30)
    key = ns + "." + name
    hit = _toggle_cache.get(key)
    if hit and time.time() - hit[1] < ttl:
        return hit[0]
    proj = os.environ.get("SIMCLOUD_PROJECT", "")
    env = settings()["_env"]
    try:
        val = _platform_get("/v1/projects/%s/envs/%s/kv/%s/keys/%s" % (proj, env, store, key))["value"]
    except ToggleError:
        if default is not None:
            return default
        raise
    _toggle_cache[key] = (val, time.time())
    return val


def stopwatch():
    t = time.perf_counter()
    return lambda: round((time.perf_counter() - t) * 1000, 2)


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]
