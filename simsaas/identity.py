"""Passkeep ID: a fictional OAuth 2.1 / OpenID Connect provider.

Conformance choices (strict readings of RFC 9700, OAuth 2.1 and OIDC Core):
- Authorization code flow only, with PKCE S256 required for every client. `response_type=token`
  (implicit) and `grant_type=password` are rejected.
- Redirect URIs must match a registered URI exactly.
- Codes are single-use and live 60 s; presenting a code twice revokes every token it produced.
- Refresh tokens rotate on every use. Presenting an already-used refresh token revokes the whole
  token family (reuse detection).
- Access tokens and ID tokens are RS256 JWTs (`typ` at+jwt / JWT) with short lifetimes; keys at
  /jwks. Revocation (RFC 7009) and introspection (RFC 7662) are supported.
- No browser: `POST /authorize` takes the user's credentials with the authorization request and
  answers with the redirect a browser would have followed (303 + Location).

Every security-relevant event goes to the operator event log (`GET /admin/events`).
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

CODE_TTL = 60
ACCESS_TTL = 300
REFRESH_TTL = 30 * 86400


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return base64.b64encode(salt + dk).decode()


def _check_password(password: str, stored: str) -> bool:
    raw = base64.b64decode(stored)
    return hmac.compare_digest(_hash_password(password, raw[:16]), stored)


@dataclass
class Client:
    client_id: str
    redirect_uris: list[str]
    scopes: list[str]
    secret_hash: str | None = None  # None = public client
    audience: str | None = None


@dataclass
class State:
    clients: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)       # username -> {password_hash, sub, claims}
    codes: dict = field(default_factory=dict)       # code -> {...}
    refresh: dict = field(default_factory=dict)     # token -> {family, used, revoked, ...}
    families: dict = field(default_factory=dict)    # family -> {revoked, access_jtis}
    revoked_jtis: set = field(default_factory=set)
    events: list = field(default_factory=list)


class Passkeep:
    def __init__(self, issuer: str, clock=time.time):
        self.issuer = issuer.rstrip("/")
        self.now = clock
        self.state = State()
        self._lock = threading.Lock()
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._kid = "pk-" + secrets.token_hex(4)

    # ---- setup (operator) ---------------------------------------------------------------

    def add_client(self, client_id: str, redirect_uris: list[str], scopes: list[str], secret: str | None = None,
                   audience: str | None = None) -> None:
        self.state.clients[client_id] = Client(client_id, redirect_uris, scopes,
                                               _hash_password(secret) if secret else None, audience)

    def add_user(self, username: str, password: str, claims: dict | None = None) -> str:
        sub = "usr_" + hashlib.sha256(username.encode()).hexdigest()[:16]
        self.state.users[username] = {"password_hash": _hash_password(password), "sub": sub, "claims": claims or {}}
        return sub

    def event(self, kind: str, **detail) -> None:
        self.state.events.append({"ts": self.now(), "event": kind, **detail})

    # ---- tokens -------------------------------------------------------------------------------

    def _jwt(self, claims: dict, typ: str) -> str:
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self._kid, "typ": typ})

    def _issue(self, client: Client, user: dict, scope: str, family: str, nonce: str | None = None,
               with_refresh: bool = True) -> dict:
        now = int(self.now())
        jti = secrets.token_hex(8)
        access = self._jwt({"iss": self.issuer, "sub": user["sub"], "aud": client.audience or client.client_id,
                            "client_id": client.client_id, "scope": scope, "iat": now, "nbf": now,
                            "exp": now + ACCESS_TTL, "jti": jti, **user["claims"]}, "at+jwt")
        self.state.families.setdefault(family, {"revoked": False, "access_jtis": []})["access_jtis"].append(jti)
        out = {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": scope}
        if "openid" in scope.split():
            out["id_token"] = self._jwt({"iss": self.issuer, "sub": user["sub"], "aud": client.client_id,
                                         "iat": now, "exp": now + ACCESS_TTL,
                                         **({"nonce": nonce} if nonce else {}), **user["claims"]}, "JWT")
        if with_refresh:
            rt = "rt_" + secrets.token_urlsafe(32)
            self.state.refresh[rt] = {"family": family, "used": False, "client_id": client.client_id,
                                      "username": next(u for u, v in self.state.users.items() if v is user),
                                      "scope": scope, "expires_at": now + REFRESH_TTL}
            out["refresh_token"] = rt
        return out

    def revoke_family(self, family: str, reason: str) -> None:
        fam = self.state.families.setdefault(family, {"revoked": False, "access_jtis": []})
        fam["revoked"] = True
        self.state.revoked_jtis.update(fam["access_jtis"])
        for rt, rec in self.state.refresh.items():
            if rec["family"] == family:
                rec["revoked"] = True
        self.event("family_revoked", family=family, reason=reason)

    def jwks(self) -> dict:
        import json
        k = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        return {"keys": [{**k, "kid": self._kid, "alg": "RS256", "use": "sig"}]}

    def verify_access(self, token: str, audience: str) -> dict:
        claims = jwt.decode(token, self._key.public_key(), algorithms=["RS256"], audience=audience, issuer=self.issuer,
                            options={"verify_exp": False})
        if claims["exp"] <= self.now() or claims["jti"] in self.state.revoked_jtis:
            raise jwt.InvalidTokenError("expired or revoked")
        return claims


def _err(status: int, error: str, description: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


def create_app(pk: Passkeep, admin_token: str) -> FastAPI:
    app = FastAPI(title="Passkeep ID")
    st = pk.state

    @app.get("/.well-known/openid-configuration")
    def discovery():
        i = pk.issuer
        return {"issuer": i, "authorization_endpoint": f"{i}/authorize", "token_endpoint": f"{i}/token",
                "jwks_uri": f"{i}/jwks", "userinfo_endpoint": f"{i}/userinfo", "revocation_endpoint": f"{i}/revoke",
                "introspection_endpoint": f"{i}/introspect", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
                "code_challenge_methods_supported": ["S256"], "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"]}

    @app.get("/jwks")
    def jwks():
        return pk.jwks()

    @app.post("/authorize")
    def authorize(response_type: str = Form(...), client_id: str = Form(...), redirect_uri: str = Form(...),
                  scope: str = Form("openid"), state: str = Form(None), nonce: str = Form(None),
                  code_challenge: str = Form(None), code_challenge_method: str = Form(None),
                  username: str = Form(...), password: str = Form(...)):
        client = st.clients.get(client_id)
        if client is None:
            return _err(400, "invalid_client", "unknown client")
        if redirect_uri not in client.redirect_uris:  # exact match; never redirect to an unregistered URI
            pk.event("redirect_uri_mismatch", client_id=client_id)
            return _err(400, "invalid_request", "redirect_uri does not exactly match a registered URI")
        if response_type != "code":
            return RedirectResponse(redirect_uri + "?" + urlencode(
                {"error": "unsupported_response_type", **({"state": state} if state else {})}), 303)
        if not code_challenge or code_challenge_method != "S256":
            return RedirectResponse(redirect_uri + "?" + urlencode(
                {"error": "invalid_request", "error_description": "PKCE with S256 is required",
                 **({"state": state} if state else {})}), 303)
        bad_scopes = set(scope.split()) - set(client.scopes)
        if bad_scopes:
            return RedirectResponse(redirect_uri + "?" + urlencode({"error": "invalid_scope", "state": state or ""}), 303)
        user = st.users.get(username)
        if not user or not _check_password(password, user["password_hash"]):
            pk.event("login_failed", client_id=client_id)
            return RedirectResponse(redirect_uri + "?" + urlencode({"error": "access_denied", "state": state or ""}), 303)
        code = "ac_" + secrets.token_urlsafe(24)
        st.codes[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "username": username, "scope": scope,
                          "nonce": nonce, "challenge": code_challenge, "expires_at": pk.now() + CODE_TTL,
                          "used": False, "family": "fam_" + secrets.token_hex(8)}
        pk.event("code_issued", client_id=client_id, sub=user["sub"])
        return RedirectResponse(redirect_uri + "?" + urlencode({"code": code, **({"state": state} if state else {})}),
                                303)

    def _client_auth(request: Request, form: dict, authorization: str | None) -> Client | JSONResponse:
        cid, secret = form.get("client_id"), form.get("client_secret")
        if authorization and authorization.lower().startswith("basic "):
            try:
                cid, secret = base64.b64decode(authorization[6:]).decode().split(":", 1)
            except Exception:
                return _err(401, "invalid_client", "malformed basic auth")
        client = st.clients.get(cid or "")
        if client is None:
            return _err(401, "invalid_client", "unknown client")
        if client.secret_hash is not None and not (secret and _check_password(secret, client.secret_hash)):
            return _err(401, "invalid_client", "client authentication failed")
        return client

    @app.post("/token")
    async def token(request: Request, authorization: str | None = Header(None)):
        form = dict(await request.form())
        grant = form.get("grant_type")
        client = _client_auth(request, form, authorization)
        if isinstance(client, JSONResponse):
            return client
        with pk._lock:
            if grant == "authorization_code":
                code = st.codes.get(form.get("code", ""))
                if not code or code["client_id"] != client.client_id:
                    return _err(400, "invalid_grant", "unknown code")
                if code["used"]:
                    pk.revoke_family(code["family"], "authorization code replayed")
                    return _err(400, "invalid_grant", "code already used")
                if code["expires_at"] <= pk.now():
                    return _err(400, "invalid_grant", "code expired")
                if form.get("redirect_uri") != code["redirect_uri"]:
                    return _err(400, "invalid_grant", "redirect_uri mismatch")
                verifier = form.get("code_verifier") or ""
                expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if not verifier or not hmac.compare_digest(expected, code["challenge"]):
                    return _err(400, "invalid_grant", "PKCE verification failed")
                code["used"] = True
                user = st.users[code["username"]]
                pk.event("tokens_issued", client_id=client.client_id, sub=user["sub"], grant=grant)
                return pk._issue(client, user, code["scope"], code["family"], code["nonce"])
            if grant == "refresh_token":
                rec = st.refresh.get(form.get("refresh_token", ""))
                if not rec or rec["client_id"] != client.client_id:
                    return _err(400, "invalid_grant", "unknown refresh token")
                if rec.get("revoked") or st.families.get(rec["family"], {}).get("revoked"):
                    return _err(400, "invalid_grant", "refresh token revoked")
                if rec["used"]:
                    pk.revoke_family(rec["family"], "refresh token reuse")
                    pk.event("refresh_reuse_detected", client_id=client.client_id, family=rec["family"])
                    return _err(400, "invalid_grant", "refresh token already used")
                if rec["expires_at"] <= pk.now():
                    return _err(400, "invalid_grant", "refresh token expired")
                rec["used"] = True
                user = st.users[rec["username"]]
                pk.event("tokens_refreshed", client_id=client.client_id, sub=user["sub"])
                return pk._issue(client, user, rec["scope"], rec["family"])
            if grant == "client_credentials":
                if client.secret_hash is None:
                    return _err(400, "unauthorized_client", "public clients cannot use client_credentials")
                svc = {"password_hash": "", "sub": f"client:{client.client_id}", "claims": {}}
                st.users.setdefault(f"__client__{client.client_id}", svc)
                pk.event("tokens_issued", client_id=client.client_id, grant=grant)
                return pk._issue(client, st.users[f"__client__{client.client_id}"], form.get("scope", ""),
                                 "fam_" + secrets.token_hex(8), with_refresh=False)
        return _err(400, "unsupported_grant_type", f"grant_type {grant!r} is not supported")

    @app.get("/userinfo")
    def userinfo(authorization: str | None = Header(None)):
        if not authorization or not authorization.lower().startswith("bearer "):
            return _err(401, "invalid_token", "missing bearer token")
        try:
            claims = jwt.decode(authorization[7:], pk._key.public_key(), algorithms=["RS256"], issuer=pk.issuer,
                                options={"verify_aud": False, "verify_exp": False})
        except jwt.PyJWTError:
            return _err(401, "invalid_token", "bad token")
        if claims["exp"] <= pk.now() or claims.get("jti") in st.revoked_jtis:
            return _err(401, "invalid_token", "expired or revoked")
        return {k: v for k, v in claims.items() if k not in ("aud", "client_id", "scope", "iat", "nbf", "exp", "jti")}

    @app.post("/revoke")
    async def revoke(request: Request, authorization: str | None = Header(None)):
        form = dict(await request.form())
        client = _client_auth(request, form, authorization)
        if isinstance(client, JSONResponse):
            return client
        tok = form.get("token", "")
        rec = st.refresh.get(tok)
        if rec and rec["client_id"] == client.client_id:
            pk.revoke_family(rec["family"], "revoked by client")
        else:
            try:
                claims = jwt.decode(tok, pk._key.public_key(), algorithms=["RS256"], options={"verify_aud": False,
                                                                                            "verify_exp": False})
                if claims.get("client_id") == client.client_id:
                    st.revoked_jtis.add(claims["jti"])
                    pk.event("access_token_revoked", client_id=client.client_id)
            except jwt.PyJWTError:
                pass
        return JSONResponse({}, status_code=200)  # RFC 7009: 200 even for unknown tokens

    @app.post("/introspect")
    async def introspect(request: Request, authorization: str | None = Header(None)):
        form = dict(await request.form())
        client = _client_auth(request, form, authorization)
        if isinstance(client, JSONResponse):
            return client
        tok = form.get("token", "")
        rec = st.refresh.get(tok)
        if rec:
            active = not rec["used"] and not rec.get("revoked") and rec["expires_at"] > pk.now()
            return {"active": active, **({"client_id": rec["client_id"], "token_type": "refresh_token"} if active else {})}
        try:
            claims = jwt.decode(tok, pk._key.public_key(), algorithms=["RS256"], options={"verify_aud": False,
                                                                                        "verify_exp": False})
        except jwt.PyJWTError:
            return {"active": False}
        active = claims["exp"] > pk.now() and claims["jti"] not in st.revoked_jtis
        return {"active": active, **({k: claims[k] for k in ("sub", "client_id", "scope", "exp", "aud")} if active else {})}

    @app.get("/admin/events")
    def admin_events(authorization: str | None = Header(None)):
        if authorization != f"Bearer {admin_token}":
            return _err(403, "forbidden", "operator only")
        return {"events": st.events}

    return app
