"""The SimCloud image registry: OCI images for managed Kubernetes, built without Docker.

    sc build <repository> --source DIR [--base python-web:3.13] [--tag v2] [--cmd ...] [--workdir /app]

`repository:push` builds an image: the base image's layers plus one new layer holding the source
tree (deterministic: sorted entries, fixed mtimes, root-owned), with the base's config updated
(WorkingDir, Cmd, Env, labels). The result is addressed as

    registry.simcloud.internal/<project>/<repository>@sha256:<digest>

Clusters pull through the OCI distribution API (pull only, anonymous inside the private network)
served on its own port. Base images ship with the platform as OCI layouts and are imported under
`library/` at start, so pods can also pull `python:3.13-slim` etc. with no internet.

Tags are mutable unless the repository says `tag_mutability: immutable`; digests never change.
"""

import gzip
import hashlib
import io
import json
import platform
import tarfile
from pathlib import Path

from .core import SimCloud
from .errors import SimCloudError
from .identity import Principal
from .kinds import PROJECT_SCOPE
from .store import srn

NS = "registry"
HOST = "registry.simcloud.internal"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(),
                                                                                         platform.machine())


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


class Registry:
    def __init__(self, cloud: SimCloud, data_dir: Path, base_dir: Path | None = None):
        self.cloud = cloud
        self.store = cloud.store
        self.blobs = data_dir / "registry" / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        if base_dir and base_dir.is_dir():
            self.import_base_images(base_dir)

    # ---- blobs and manifests ------------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        digest = sha256(data)
        path = self.blob_path(digest)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.rename(path)
        return digest

    def blob_path(self, digest: str) -> Path:
        algo, _, hexd = digest.partition(":")
        if algo != "sha256" or len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
            raise SimCloudError("invalid_request", f"bad digest {digest!r}")
        return self.blobs / hexd

    def _repo(self, name: str) -> dict:
        return self.store.kv_get(NS, name) or {"tags": {}, "images": {}}

    def _save_repo(self, name: str, repo: dict) -> None:
        self.store.kv_put(NS, name, repo)

    def resolve(self, name: str, reference: str) -> tuple[str, bytes, str]:
        """(digest, manifest bytes, media type) for a tag or digest in repository `name`."""
        repo = self._repo(name)
        digest = reference if reference.startswith("sha256:") else repo["tags"].get(reference)
        if not digest or digest not in repo["images"]:
            raise SimCloudError("not_found", f"manifest {name}:{reference} not found")
        data = self.blob_path(digest).read_bytes()
        return digest, data, json.loads(data).get("mediaType", OCI_MANIFEST)

    def _record(self, name: str, digest: str, tag: str | None, info: dict, immutable: bool = False) -> None:
        repo = self._repo(name)
        if tag:
            old = repo["tags"].get(tag)
            if old and old != digest and immutable:
                raise SimCloudError("conflict", f"tag {tag} already points at {old} and the repository's tags "
                                    "are immutable", {"tag": tag, "digest": old})
            repo["tags"][tag] = digest
        repo["images"].setdefault(digest, {**info, "created_at": self.cloud.clock.now()})
        self._save_repo(name, repo)

    # ---- base images --------------------------------------------------------------------

    def import_base_images(self, base_dir: Path) -> list[str]:
        """Import every OCI layout under base_dir/<name>/<tag>/ as library/<name>:<tag>."""
        done = []
        for layout in sorted(p for p in base_dir.glob("*/*") if (p / "index.json").exists()):
            name, tag = layout.parent.name, layout.name
            index = json.loads((layout / "index.json").read_text())
            manifest_desc = self._pick(layout, index)
            for blob in (layout / "blobs" / "sha256").iterdir():
                target = self.blobs / blob.name
                if not target.exists():
                    target.write_bytes(blob.read_bytes())
            self._record(f"library/{name}", manifest_desc["digest"], tag, {"base": True})
            done.append(f"library/{name}:{tag}")
        # Overlays: a base plus a prebuilt layer directory (e.g. python-web = python + web deps).
        for overlay in sorted(base_dir.glob("*.overlay.json")):
            spec = json.loads(overlay.read_text())
            root = base_dir / spec["layer_dir"]
            if root.is_dir():
                layer = _layer_from_dir(root, prefix="")
                digest, _ = self._compose(spec["base"], layer, {"Env": spec.get("env", {})}, "base image overlay")
                self._record(f"library/{spec['name']}", digest, spec["tag"], {"base": True})
                done.append(f"library/{spec['name']}:{spec['tag']}")
        return done

    def _pick(self, layout: Path, index: dict) -> dict:
        manifests = index.get("manifests", [])
        if not manifests:
            raise SimCloudError("internal", f"empty OCI layout {layout}")
        desc = manifests[0]
        data = (layout / "blobs" / "sha256" / desc["digest"].split(":")[1]).read_bytes()
        if json.loads(data).get("mediaType") == OCI_INDEX or desc.get("mediaType") == OCI_INDEX:
            sub = json.loads(data)["manifests"]
            desc = next((m for m in sub if m.get("platform", {}).get("architecture") == ARCH), sub[0])
        return desc

    # ---- building -----------------------------------------------------------------------

    def _base_ref(self, base: str) -> tuple[str, str]:
        ref = base.removeprefix(HOST + "/").removeprefix("docker.io/")
        if "@" in ref:
            name, tag = ref.split("@", 1)
        elif ":" in ref.rsplit("/", 1)[-1]:
            name, tag = ref.rsplit(":", 1)
        else:
            name, tag = ref, "latest"
        if "/" not in name:
            name = f"library/{name}"
        return name, tag

    def _compose(self, base: str, layer: bytes, changes: dict, comment: str) -> tuple[str, dict]:
        base_name, base_ref = self._base_ref(base)
        _, mbytes, _ = self.resolve(base_name, base_ref)
        base_manifest = json.loads(mbytes)
        config = json.loads(self.blob_path(base_manifest["config"]["digest"]).read_bytes())
        diff_id = sha256(gzip.decompress(layer))
        layer_digest = self.put_blob(layer)
        cfg = config.setdefault("config", {})
        env = [e for e in cfg.get("Env") or [] if e.split("=", 1)[0] not in (changes.get("Env") or {})]
        cfg["Env"] = env + [f"{k}={v}" for k, v in sorted((changes.get("Env") or {}).items())]
        for k in ("WorkingDir", "Cmd", "Entrypoint", "User", "ExposedPorts"):
            if k in changes:
                cfg[k] = changes[k]
        cfg["Labels"] = {**(cfg.get("Labels") or {}), **(changes.get("Labels") or {})}
        config["rootfs"]["diff_ids"] = config["rootfs"]["diff_ids"] + [diff_id]
        config["history"] = (config.get("history") or []) + [{"created_by": f"simcloud: {comment}"}]
        config.pop("created", None)
        config_bytes = _canonical(config)
        manifest = {"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                    "config": {"mediaType": OCI_CONFIG, "digest": self.put_blob(config_bytes),
                               "size": len(config_bytes)},
                    "layers": base_manifest["layers"] + [{"mediaType": OCI_LAYER, "digest": layer_digest,
                                                          "size": len(layer)}]}
        mb = _canonical(manifest)
        return self.put_blob(mb), manifest

    def build(self, actor: Principal, project: str, repository: str, archive: bytes, base: str,
              tag: str | None = None, cmd: list[str] | None = None, workdir: str = "/app",
              env: dict[str, str] | None = None, port: int | None = None) -> dict:
        resource = srn(project, PROJECT_SCOPE, "repository", repository)
        self.cloud._scope(project, PROJECT_SCOPE, "repository")
        self.cloud.authorize(actor, "repository:push", resource, project)
        repo_res = self.store.get(project, PROJECT_SCOPE, "repository", repository)
        if not repo_res:
            raise SimCloudError("not_found", f"repository/{repository} not found in {project}")
        if not workdir.startswith("/"):
            raise SimCloudError("invalid_request", "workdir must be an absolute path")
        try:
            layer = _layer_from_archive(archive, workdir)
        except (tarfile.TarError, OSError, EOFError) as e:
            raise SimCloudError("invalid_request", f"the source must be a gzip tar: {e}")
        source_digest = sha256(archive)
        changes = {"WorkingDir": workdir, "Env": env or {},
                   "Labels": {"org.simcloud.source": source_digest, "org.simcloud.built-by": actor.name}}
        if cmd:
            changes["Cmd"], changes["Entrypoint"] = cmd, None
        if port:
            changes["ExposedPorts"] = {f"{port}/tcp": {}}
        try:
            digest, manifest = self._compose(base, layer, changes, f"build {repository} from {source_digest[:19]}")
        except SimCloudError as e:
            if e.code == "not_found":
                raise SimCloudError("not_found", f"base image {base!r} not found",
                                    {"available": self.base_images()})
            raise
        name = f"{project}/{repository}"
        self._record(name, digest, tag, {"source": source_digest, "base": base, "built_by": actor.name},
                     immutable=repo_res["spec"].get("tag_mutability") == "immutable")
        ref = f"{HOST}/{name}@{digest}"
        return {"repository": name, "digest": digest, "tag": tag, "image": ref,
                "tagged": f"{HOST}/{name}:{tag}" if tag else None,
                "size": sum(l["size"] for l in manifest["layers"])}

    def images(self, actor: Principal, project: str, repository: str) -> dict:
        self.cloud._scope(project, PROJECT_SCOPE, "repository")
        self.cloud.authorize(actor, "repository:pull", srn(project, PROJECT_SCOPE, "repository", repository), project)
        repo = self._repo(f"{project}/{repository}")
        return {"repository": f"{project}/{repository}", "tags": repo["tags"],
                "images": [{"digest": d, **info} for d, info in repo["images"].items()]}

    def base_images(self) -> list[str]:
        return sorted(f"{k.removeprefix('library/')}:{t}" for k, r in self.store.kv_items(NS)
                      if k.startswith("library/") for t in r["tags"])


def _tarinfo(name: str, data: bytes | None, mode: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "root", "root"
    ti.mode = mode
    if data is None:
        ti.type = tarfile.DIRTYPE
    else:
        ti.size = len(data)
    return ti


def _write_layer(entries: list[tuple[str, bytes | None, int]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data, mode in sorted(entries):
            tar.addfile(_tarinfo(name, data, mode), io.BytesIO(data) if data is not None else None)
    return gzip.compress(raw.getvalue(), mtime=0)


def _layer_from_archive(archive: bytes, workdir: str) -> bytes:
    prefix = workdir.strip("/")
    entries, dirs = [], set()
    parts = prefix.split("/")
    for i in range(1, len(parts) + 1):
        dirs.add("/".join(parts[:i]))
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as src:
        for m in src.getmembers():
            name = m.name.lstrip("./").lstrip("/")
            if not name or ".." in Path(name).parts:
                continue
            full = f"{prefix}/{name}"
            if m.isdir():
                dirs.add(full)
            elif m.isfile():
                data = src.extractfile(m).read()
                entries.append((full, data, 0o755 if m.mode & 0o111 else 0o644))
                p = Path(full).parent
                while str(p) not in (".", ""):
                    dirs.add(str(p))
                    p = p.parent
    entries += [(d, None, 0o755) for d in dirs]
    return _write_layer(entries)


def _layer_from_dir(root: Path, prefix: str) -> bytes:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        rel = str(Path(prefix) / path.relative_to(root)).lstrip("/")
        if path.is_dir():
            entries.append((rel, None, 0o755))
        else:
            entries.append((rel, path.read_bytes(), 0o755 if path.stat().st_mode & 0o111 else 0o644))
    return _write_layer(entries)


def create_registry_app(registry: Registry):
    """The pull side of the OCI distribution API."""
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse, Response

    app = FastAPI(title="SimCloud registry", docs_url=None, redoc_url=None, openapi_url=None)

    def _err(code: str, msg: str, status: int):
        return JSONResponse({"errors": [{"code": code, "message": msg}]}, status_code=status)

    @app.api_route("/v2/", methods=["GET", "HEAD"])
    def base():
        return JSONResponse({}, headers={"Docker-Distribution-API-Version": "registry/2.0"})

    @app.api_route("/v2/{name:path}/manifests/{reference}", methods=["GET", "HEAD"])
    def manifest(name: str, reference: str, request: Request):
        try:
            digest, data, media = registry.resolve(name, reference)
        except SimCloudError:
            return _err("MANIFEST_UNKNOWN", f"{name}:{reference}", 404)
        headers = {"Docker-Content-Digest": digest, "Content-Length": str(len(data))}
        body = b"" if request.method == "HEAD" else data
        return Response(body, media_type=media, headers=headers)

    @app.api_route("/v2/{name:path}/blobs/{digest}", methods=["GET", "HEAD"])
    def blob(name: str, digest: str, request: Request):
        try:
            path = registry.blob_path(digest)
        except SimCloudError:
            return _err("DIGEST_INVALID", digest, 400)
        if not path.exists():
            return _err("BLOB_UNKNOWN", digest, 404)
        headers = {"Docker-Content-Digest": digest}
        if request.method == "HEAD":
            return Response(b"", headers={**headers, "Content-Length": str(path.stat().st_size)},
                            media_type="application/octet-stream")
        return FileResponse(path, media_type="application/octet-stream", headers=headers)

    return app
