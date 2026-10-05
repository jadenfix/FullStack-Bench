"""Workload identity: federation in, identity tokens out.

In: an external workload (CI, another cloud) presents an OIDC JWT from an
issuer the platform operator registered. A `trust` resource says which
subjects may become which service account. Validation follows RFC 8725:
the algorithm is pinned (RS256/ES256, never 'none' or HMAC), the key comes
from the registered JWKS by `kid`, and iss, aud, exp and nbf are checked with
60 s of clock skew. The result is a short-lived SimCloud token.

Out: SimCloud signs identity tokens (ES256) for its service accounts so
services can authenticate to each other; the JWKS is public.

Long-lived service-account keys exist too, because real platforms have them.
Tasks that require short-lived credentials grade their absence.
"""

import json
import os
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .kinds import PROJECT_SCOPE
from .policy import matches
from .store import srn

ISSUERS = "oidc_issuers"
ALLOWED_ALGS = ["RS256", "ES256"]
SKEW = 60
SIMCLOUD_ISSUER = "https://oidc.simcloud.internal"


class Federation:
    def __init__(self, cloud: SimCloud, signing_key_pem: bytes | None = None):
        self.cloud = cloud
        self.store = cloud.store
        self.clock = cloud.clock
        key = (serialization.load_pem_private_key(signing_key_pem, None) if signing_key_pem
               else ec.generate_private_key(ec.SECP256R1()))
        self._signing_key = key
        self._kid = "simcloud-1"

    # ---- issuer registry (platform operator) ------------------------------------

    def register_issuer(self, actor: Principal, issuer: str, jwks: dict) -> dict:
        self.cloud._require_admin(actor, "issuer:register", issuer)
        keys = jwks.get("keys") or []
        if not keys or any("kid" not in k for k in keys):
            raise SimCloudError("invalid_request", "jwks must have keys, each with a 'kid'")
        self.store.kv_put(ISSUERS, issuer, {"issuer": issuer, "jwks": jwks})
        return {"issuer": issuer, "keys": [k["kid"] for k in keys]}

    def _issuer_key(self, issuer: str, kid: str | None):
        entry = self.store.kv_get(ISSUERS, issuer)
        if not entry:
            raise SimCloudError("unauthenticated", f"issuer {issuer!r} is not trusted by this platform")
        for k in entry["jwks"]["keys"]:
            if k["kid"] == kid:
                if k.get("kty") == "RSA":
                    return RSAAlgorithm.from_jwk(json.dumps(k))
                if k.get("kty") == "EC":
                    return ECAlgorithm.from_jwk(json.dumps(k))
        raise SimCloudError("unauthenticated", f"no key {kid!r} for issuer {issuer!r}")

    # ---- token exchange ---------------------------------------------------------

    def exchange(self, project: str, subject_token: str, trust_name: str | None = None,
                 ttl_seconds: int | None = None) -> dict:
        self.cloud.project(project)
        try:
            header = jwt.get_unverified_header(subject_token)
            unverified = jwt.decode(subject_token, options={"verify_signature": False})
        except jwt.PyJWTError as e:
            raise SimCloudError("unauthenticated", f"malformed subject token: {e}")
        if header.get("alg") not in ALLOWED_ALGS:
            raise SimCloudError("unauthenticated", f"algorithm {header.get('alg')!r} not allowed; use RS256 or ES256")
        issuer = unverified.get("iss")
        trusts = [t for t in self.store.list_resources(project, PROJECT_SCOPE, "trust")
                  if (trust_name is None or t["name"] == trust_name) and t["spec"]["issuer"] == issuer]
        if not trusts:
            self._audit_exchange(project, issuer, unverified.get("sub"), "denied", "no trust for this issuer")
            raise SimCloudError("access_denied", f"no trust in {project} accepts issuer {issuer!r}")
        key = self._issuer_key(issuer, header.get("kid"))
        failures = []
        for trust in trusts:
            spec = trust["spec"]
            try:
                # Signature, alg, aud and iss are verified here; time claims below use SimCloud's clock.
                claims = jwt.decode(subject_token, key, algorithms=[header["alg"]], audience=spec["audience"],
                                    issuer=spec["issuer"],
                                    options={"verify_exp": False, "verify_nbf": False, "verify_iat": False,
                                             "require": ["exp", "iat", "sub", "aud", "iss"]})
            except jwt.PyJWTError as e:
                failures.append(f"{trust['name']}: {e}")
                continue
            now = self.clock.now()
            if claims["exp"] + SKEW <= now or claims.get("nbf", 0) - SKEW > now:
                failures.append(f"{trust['name']}: token expired or not yet valid")
                continue
            if not matches(spec["subject"], claims["sub"]):
                failures.append(f"{trust['name']}: subject {claims['sub']!r} does not match {spec['subject']!r}")
                continue
            if any(str(claims.get(k)) != v for k, v in spec["claims"].items()):
                failures.append(f"{trust['name']}: required claims do not match")
                continue
            sa = spec["service_account"]
            if not self.store.get(project, PROJECT_SCOPE, "service_account", sa):
                failures.append(f"{trust['name']}: service account {sa!r} does not exist")
                continue
            ttl = min(ttl_seconds or spec["max_ttl_seconds"], spec["max_ttl_seconds"])
            carried = {k: str(v) for k, v in claims.items() if k not in ("exp", "iat", "nbf", "aud", "jti")}
            token, record = self.cloud.tokens.issue(f"service-account:{sa}", project, ttl,
                                                    claims={**carried, "trust": trust["name"]}, kind="federated")
            self._audit_exchange(project, issuer, claims["sub"], "allowed", trust["name"], sa, record["id"])
            return {"access_token": token, "token_type": "Bearer", "expires_in": ttl,
                    "principal": f"service-account:{sa}"}
        self._audit_exchange(project, issuer, unverified.get("sub"), "denied", "; ".join(failures))
        raise SimCloudError("access_denied", "no trust accepted this token", {"reasons": failures})

    def _audit_exchange(self, project, issuer, sub, outcome, reason, sa=None, token_id=None):
        self.store.audit(f"oidc:{sub}", "federation:exchange", srn(project, PROJECT_SCOPE, "trust", "*"), outcome,
                         {"issuer": issuer, "subject": sub, "reason": reason, "service_account": sa,
                          "token_id": token_id})

    # ---- service-account credentials --------------------------------------------

    def _sa(self, actor: Principal, project: str, name: str, verb: str) -> None:
        res = srn(project, PROJECT_SCOPE, "service_account", name)
        self.cloud.authorize(actor, f"service_account:{verb}", res, project)
        if not self.store.get(project, PROJECT_SCOPE, "service_account", name):
            raise SimCloudError("not_found", f"service account {name} not found")

    def create_key(self, actor: Principal, project: str, name: str) -> dict:
        """A long-lived key. Shown once."""
        self._sa(actor, project, name, "create_key")
        token, record = self.cloud.tokens.issue(f"service-account:{name}", project, None, kind="sa_key")
        return {"key": token, "id": record["id"], "expires_at": None}

    def short_lived_token(self, actor: Principal, project: str, name: str, ttl_seconds: int = 900) -> dict:
        self._sa(actor, project, name, "impersonate")
        ttl = max(60, min(ttl_seconds, 3600))
        token, record = self.cloud.tokens.issue(f"service-account:{name}", project, ttl,
                                                claims={"impersonated_by": actor.name}, kind="impersonation")
        return {"access_token": token, "expires_in": ttl, "id": record["id"]}

    def id_token(self, actor: Principal, project: str, name: str, audience: str, ttl_seconds: int = 600) -> dict:
        self._sa(actor, project, name, "impersonate")
        now = int(self.clock.now())
        claims = {"iss": SIMCLOUD_ISSUER, "sub": f"service-account:{project}/{name}", "aud": audience,
                  "iat": now, "nbf": now, "exp": now + max(60, min(ttl_seconds, 3600)), "project": project}
        token = jwt.encode(claims, self._signing_key, algorithm="ES256", headers={"kid": self._kid})
        return {"id_token": token, "expires_at": claims["exp"]}

    def jwks(self) -> dict:
        jwk = json.loads(ECAlgorithm.to_jwk(self._signing_key.public_key()))
        return {"keys": [{**jwk, "kid": self._kid, "alg": "ES256", "use": "sig"}]}

    def openid_configuration(self) -> dict:
        return {"issuer": SIMCLOUD_ISSUER, "jwks_uri": "/v1/oidc/jwks", "id_token_signing_alg_values_supported": ["ES256"]}


def load_or_create_signing_key(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path.write_bytes(pem)
    os.chmod(path, 0o600)
    return pem
