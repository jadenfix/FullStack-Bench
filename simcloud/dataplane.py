"""Data-plane operations: secrets, KV, queues, topics and object storage.

Each operation checks the resource exists, authorises the matching action
(`secret:access`, `queue:receive`, ...) and is audited. Secret values never
appear in resources, audit records or errors.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets as pysecrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .store import srn

SECRETS, KVDATA, QUEUES, OBJECTS = "secret_versions", "kv_data", "queue_messages", "objects"
MAX_VALUE_BYTES = 256 * 1024


class DataPlane:
    def __init__(self, cloud: SimCloud, kms_key: bytes | None = None, url_signing_key: bytes | None = None):
        self.cloud = cloud
        self.store = cloud.store
        self.clock = cloud.clock
        self._aead = AESGCM(kms_key or AESGCM.generate_key(bit_length=256))
        self._url_key = url_signing_key or pysecrets.token_bytes(32)

    def _resource(self, actor: Principal, project: str, env: str, kind: str, name: str, verb: str) -> dict:
        self.cloud._scope(project, env, kind)
        self.cloud.authorize(actor, f"{kind}:{verb}", srn(project, env, kind, name), project, env)
        r = self.store.get(project, env, kind, name)
        if not r:
            raise SimCloudError("not_found", f"{kind}/{name} not found in {project}/{env}")
        return r

    # ---- secrets -------------------------------------------------------------

    def _seal(self, aad: str, value: str) -> str:
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, value.encode(), aad.encode())).decode()

    def _open(self, aad: str, sealed: str) -> str:
        raw = base64.b64decode(sealed)
        return self._aead.decrypt(raw[:12], raw[12:], aad.encode()).decode()

    def _versions(self, project: str, env: str, name: str) -> list[tuple[str, dict]]:
        return self.store.kv_items(SECRETS, f"{project}/{env}/{name}/")

    def add_secret_version(self, actor: Principal, project: str, env: str, name: str, value: str) -> dict:
        self._resource(actor, project, env, "secret", name, "add_version")
        if len(value.encode()) > MAX_VALUE_BYTES:
            raise SimCloudError("invalid_request", "secret value exceeds 256 KiB")
        return self._add_version(project, env, name, value)

    def _add_version(self, project: str, env: str, name: str, value: str) -> dict:
        versions = self._versions(project, env, name)
        for key, v in versions:
            if v["stage"] == "current":
                v["stage"] = "previous"
            elif v["stage"] == "previous":
                v["stage"] = "deprecated"
            self.store.kv_put(SECRETS, key, v)
        number = len(versions) + 1
        aad = f"{project}/{env}/{name}/{number}"
        meta = {"version": number, "stage": "current", "created_at": self.clock.now()}
        self.store.kv_put(SECRETS, f"{project}/{env}/{name}/{number:06d}", {**meta, "sealed": self._seal(aad, value)})
        return meta

    def access_secret(self, actor: Principal, project: str, env: str, name: str, version: str = "current") -> dict:
        self._resource(actor, project, env, "secret", name, "access")
        for key, v in self._versions(project, env, name):
            if str(v["version"]) == str(version) or v["stage"] == version:
                if v["stage"] == "disabled":
                    raise SimCloudError("conflict", f"secret {name} version {v['version']} is disabled")
                value = self._open(f"{project}/{env}/{name}/{v['version']}", v["sealed"])
                return {"version": v["version"], "stage": v["stage"], "value": value}
        raise SimCloudError("not_found", f"secret {name} has no version {version!r}")

    def list_secret_versions(self, actor: Principal, project: str, env: str, name: str) -> list[dict]:
        self._resource(actor, project, env, "secret", name, "read")
        return [{k: v[k] for k in ("version", "stage", "created_at")} for _, v in self._versions(project, env, name)]

    def rotate_secret(self, actor: Principal, project: str, env: str, name: str) -> dict:
        """Generate a new random current version; the old one stays readable as 'previous'
        so clients can roll over without downtime."""
        self._resource(actor, project, env, "secret", name, "rotate")
        return self._add_version(project, env, name, pysecrets.token_urlsafe(32))

    def secret_values(self, project: str, env: str) -> list[str]:
        """Every live secret value in an environment (verifier use: scan logs for leaks)."""
        out = []
        for key, v in self.store.kv_items(SECRETS, f"{project}/{env}/"):
            name = key.split("/")[2]
            out.append(self._open(f"{project}/{env}/{name}/{v['version']}", v["sealed"]))
        return out

    # ---- kv --------------------------------------------------------------------

    def kv_put(self, actor: Principal, project: str, env: str, name: str, key: str, value,
               ttl_seconds: float | None = None) -> dict:
        r = self._resource(actor, project, env, "kv", name, "put")
        if len(json.dumps(value).encode()) > MAX_VALUE_BYTES:
            raise SimCloudError("invalid_request", "value exceeds 256 KiB")
        ttl = ttl_seconds if ttl_seconds is not None else r["spec"].get("default_ttl_seconds")
        expires = self.clock.now() + ttl if ttl else None
        self.store.kv_put(KVDATA, f"{project}/{env}/{name}/{key}", {"value": value, "expires_at": expires})
        return {"key": key, "expires_at": expires}

    def kv_get(self, actor: Principal, project: str, env: str, name: str, key: str):
        self._resource(actor, project, env, "kv", name, "get")
        item = self.store.kv_get(KVDATA, f"{project}/{env}/{name}/{key}")
        if not item or (item["expires_at"] is not None and self.clock.now() >= item["expires_at"]):
            raise SimCloudError("not_found", f"key {key!r} not found")
        return {"key": key, "value": item["value"], "expires_at": item["expires_at"]}

    def kv_remove(self, actor: Principal, project: str, env: str, name: str, key: str) -> None:
        self._resource(actor, project, env, "kv", name, "remove")
        self.store.kv_delete(KVDATA, f"{project}/{env}/{name}/{key}")

    # ---- queues ----------------------------------------------------------------

    def _q(self, project: str, env: str, name: str) -> str:
        return f"{project}/{env}/{name}/"

    def _enqueue(self, project: str, env: str, name: str, body, group: str | None = None,
                 delay: float = 0, receives: int = 0, origin: str | None = None) -> str:
        seq = self.store.kv_get("queue_seq", f"{project}/{env}/{name}") or 0
        seq += 1
        self.store.kv_put("queue_seq", f"{project}/{env}/{name}", seq)
        msg_id = f"m{seq:08d}"
        self.store.kv_put(QUEUES, self._q(project, env, name) + f"{seq:012d}", {
            "id": msg_id, "body": body, "group": group, "visible_at": self.clock.now() + delay,
            "receives": receives, "receipt": None, "sent_at": self.clock.now(), "dead_letter_source": origin,
        })
        return msg_id

    def send(self, actor: Principal, project: str, env: str, name: str, body, group: str | None = None,
             delay_seconds: float = 0) -> dict:
        r = self._resource(actor, project, env, "queue", name, "send")
        if r["spec"]["fifo"] and not group:
            raise SimCloudError("invalid_request", "FIFO queues need a message group")
        return {"id": self._enqueue(project, env, name, body, group, delay_seconds)}

    def receive(self, actor: Principal, project: str, env: str, name: str, max_messages: int = 1,
                visibility_timeout: float | None = None) -> list[dict]:
        r = self._resource(actor, project, env, "queue", name, "receive")
        spec, now = r["spec"], self.clock.now()
        vt = spec["visibility_timeout_seconds"] if visibility_timeout is None else visibility_timeout
        out, blocked_groups = [], set()
        for key, m in self.store.kv_items(QUEUES, self._q(project, env, name)):
            if len(out) >= max(1, min(max_messages, 10)):
                break
            if spec["fifo"] and m["group"] in blocked_groups:
                continue
            if m["visible_at"] > now:
                if spec["fifo"]:
                    blocked_groups.add(m["group"])  # an in-flight message blocks its group
                continue
            if spec["max_receives"] and m["receives"] >= spec["max_receives"]:
                self.store.kv_delete(QUEUES, key)
                if spec["dead_letter_queue"]:
                    self._enqueue(project, env, spec["dead_letter_queue"], m["body"], m["group"],
                                  receives=0, origin=name)
                continue
            m["receives"] += 1
            m["visible_at"] = now + vt
            m["receipt"] = pysecrets.token_hex(8)
            self.store.kv_put(QUEUES, key, m)
            if spec["fifo"]:
                blocked_groups.add(m["group"])
            out.append({"id": m["id"], "body": m["body"], "receipt": m["receipt"], "receives": m["receives"],
                        "group": m["group"]})
        return out

    def ack(self, actor: Principal, project: str, env: str, name: str, receipt: str) -> None:
        self._resource(actor, project, env, "queue", name, "ack")
        for key, m in self.store.kv_items(QUEUES, self._q(project, env, name)):
            if m["receipt"] == receipt:
                if m["visible_at"] <= self.clock.now():
                    raise SimCloudError("conflict", "receipt expired: the visibility timeout passed and the "
                                        "message may have been delivered again")
                self.store.kv_delete(QUEUES, key)
                return
        raise SimCloudError("not_found", "unknown receipt")

    def purge(self, actor: Principal, project: str, env: str, name: str) -> dict:
        self._resource(actor, project, env, "queue", name, "purge")
        items = self.store.kv_items(QUEUES, self._q(project, env, name))
        for key, _ in items:
            self.store.kv_delete(QUEUES, key)
        return {"purged": len(items)}

    def queue_depth(self, project: str, env: str, name: str) -> int:
        return len(self.store.kv_items(QUEUES, self._q(project, env, name)))

    def publish(self, actor: Principal, project: str, env: str, topic: str, body) -> dict:
        r = self._resource(actor, project, env, "topic", topic, "publish")
        ids = [self._enqueue(project, env, q, body) for q in r["spec"]["subscriptions"]
               if self.store.get(project, env, "queue", q)]
        return {"delivered_to": len(ids)}

    # ---- object storage ------------------------------------------------------------

    def put_object(self, actor: Principal, project: str, env: str, bucket: str, key: str, data: bytes,
                   content_type: str = "application/octet-stream") -> dict:
        self._resource(actor, project, env, "bucket", bucket, "put_object")
        return self._store_object(project, env, bucket, key, data, content_type)

    def _store_object(self, project, env, bucket, key, data, content_type) -> dict:
        etag = hashlib.md5(data).hexdigest()
        self.store.kv_put(OBJECTS, f"{project}/{env}/{bucket}/{key}", {
            "data": base64.b64encode(data).decode(), "content_type": content_type, "etag": etag,
            "size": len(data), "updated_at": self.clock.now()})
        return {"key": key, "etag": etag, "size": len(data)}

    def get_object(self, actor: Principal | None, project: str, env: str, bucket: str, key: str) -> dict:
        if actor is not None:
            self._resource(actor, project, env, "bucket", bucket, "get_object")
        obj = self.store.kv_get(OBJECTS, f"{project}/{env}/{bucket}/{key}")
        if not obj:
            raise SimCloudError("not_found", f"object {key!r} not found")
        return {**obj, "data": base64.b64decode(obj["data"])}

    def delete_object(self, actor: Principal, project: str, env: str, bucket: str, key: str) -> None:
        self._resource(actor, project, env, "bucket", bucket, "delete_object")
        self.store.kv_delete(OBJECTS, f"{project}/{env}/{bucket}/{key}")

    def list_objects(self, actor: Principal, project: str, env: str, bucket: str, prefix: str = "",
                     limit: int | None = None, after: str = "") -> list[dict]:
        """Objects under `prefix` in key order; with `limit`, at most that many keys strictly after `after`."""
        self._resource(actor, project, env, "bucket", bucket, "list")
        base = f"{project}/{env}/{bucket}/"
        items = [{"key": k[len(base):], "size": v["size"], "etag": v["etag"],
                  "content_type": v.get("content_type"), "updated_at": v.get("updated_at")}
                 for k, v in self.store.kv_items(OBJECTS, base + prefix)]
        items.sort(key=lambda i: i["key"])
        if after:
            items = [i for i in items if i["key"] > after]
        return items[:limit] if limit else items

    def sign_url(self, actor: Principal, project: str, env: str, bucket: str, key: str, method: str,
                 expires_in: int) -> dict:
        self._resource(actor, project, env, "bucket", bucket, "sign_url")
        if method not in ("GET", "PUT") or not 1 <= expires_in <= 7 * 86400:
            raise SimCloudError("invalid_request", "method must be GET or PUT; expires_in 1..604800 seconds")
        expires = int(self.clock.now() + expires_in)
        path = f"{project}/{env}/{bucket}/{key}"
        sig = self._sig(method, path, expires)
        return {"url": f"/v1/signed/{path}?method={method}&expires={expires}&sig={sig}", "expires_at": expires}

    def _sig(self, method: str, path: str, expires: int) -> str:
        return hmac.new(self._url_key, f"{method}\n{path}\n{expires}".encode(), hashlib.sha256).hexdigest()

    def verify_signed(self, method: str, path: str, expires: int, sig: str) -> tuple[str, str, str, str]:
        if not hmac.compare_digest(self._sig(method, path, expires), sig):
            raise SimCloudError("access_denied", "invalid signature")
        if self.clock.now() >= expires:
            raise SimCloudError("access_denied", "signed URL expired")
        project, env, bucket, key = path.split("/", 3)
        return project, env, bucket, key
