"""Principals and bearer tokens.

Tokens look like `sct_<id>_<secret>`. Only a SHA-256 of the secret is stored;
the id is a public prefix used for lookup, so a leaked database never yields
usable tokens. The admin token (verifier only) comes from the environment and
is never stored or listed.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from .clock import Clock
from .errors import SimCloudError
from .store import Store

NS = "tokens"
ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    name: str  # e.g. "user:dev", "service-account:api", "federated:ci-deploy", "admin"
    project: str | None = None  # None only for admin
    claims: dict = field(default_factory=dict)
    token_id: str | None = None

    @property
    def type(self) -> str:
        return self.name.split(":", 1)[0]

    @property
    def is_admin(self) -> bool:
        return self.name == ADMIN


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Tokens:
    def __init__(self, store: Store, clock: Clock, admin_token: str | None):
        self.store = store
        self.clock = clock
        self._admin_digest = _digest(admin_token) if admin_token else None

    def issue(self, principal: str, project: str, ttl_seconds: float | None = None,
              claims: dict | None = None, kind: str = "api_key") -> tuple[str, dict]:
        """Return (token, record). The token is shown once; only its hash is kept."""
        token_id = secrets.token_hex(4)
        secret = secrets.token_urlsafe(24)
        now = self.clock.now()
        record = {
            "id": token_id, "principal": principal, "project": project, "kind": kind,
            "digest": _digest(secret), "created_at": now,
            "expires_at": now + ttl_seconds if ttl_seconds else None,
            "claims": claims or {}, "revoked": False,
        }
        self.store.kv_put(NS, token_id, record)
        return f"sct_{token_id}_{secret}", _public(record)

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise SimCloudError("unauthenticated", "missing bearer token")
        if self._admin_digest and hmac.compare_digest(_digest(token), self._admin_digest):
            return Principal(ADMIN)
        parts = token.split("_", 2)
        if len(parts) != 3 or parts[0] != "sct":
            raise SimCloudError("unauthenticated", "malformed token")
        record = self.store.kv_get(NS, parts[1])
        if not record or not hmac.compare_digest(_digest(parts[2]), record["digest"]):
            raise SimCloudError("unauthenticated", "unknown token")
        if record["revoked"]:
            raise SimCloudError("unauthenticated", "token revoked")
        if record["expires_at"] is not None and self.clock.now() >= record["expires_at"]:
            raise SimCloudError("unauthenticated", "token expired")
        return Principal(record["principal"], record["project"], record["claims"], record["id"])

    def revoke(self, token_id: str) -> None:
        record = self.store.kv_get(NS, token_id)
        if not record:
            raise SimCloudError("not_found", f"token {token_id} not found")
        record["revoked"] = True
        self.store.kv_put(NS, token_id, record)

    def list_for_project(self, project: str) -> list[dict]:
        return [_public(r) for _, r in self.store.kv_items(NS) if r["project"] == project]


def _public(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "digest"}
