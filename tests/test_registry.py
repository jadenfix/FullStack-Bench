import gzip
import hashlib
import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from simcloud.delivery import pack_directory
from simcloud.errors import SimCloudError
from simcloud.identity import Principal
from simcloud.registry import ARCH, HOST, Registry, create_registry_app

ADMIN = Principal("admin")


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def make_base_layout(root, name="pybase", tag="1"):
    """A tiny single-arch OCI layout behind an index, like `skopeo copy ... oci:` writes."""
    layout = root / name / tag
    blobs = layout / "blobs" / "sha256"
    blobs.mkdir(parents=True)

    def put(b: bytes) -> str:
        d = _sha(b)
        (blobs / d.split(":")[1]).write_bytes(b)
        return d
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        data = b"#!/bin/sh\necho base\n"
        ti = tarfile.TarInfo("usr/bin/hello")
        ti.size, ti.mode = len(data), 0o755
        t.addfile(ti, io.BytesIO(data))
    layer = gzip.compress(raw.getvalue())
    config = json.dumps({"architecture": ARCH, "os": "linux",
                         "config": {"Env": ["PATH=/usr/bin", "LANG=C"], "Cmd": ["sh"]},
                         "rootfs": {"type": "layers", "diff_ids": [_sha(raw.getvalue())]}}).encode()
    manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": put(config),
                                      "size": len(config)},
                           "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                                       "digest": put(layer), "size": len(layer)}]}).encode()
    md = put(manifest)
    (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": md, "size": len(manifest),
         "platform": {"architecture": ARCH, "os": "linux"}}]}))
    (layout / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
    return md


@pytest.fixture
def reg(cloud, tmp_path):
    base = tmp_path / "base"
    make_base_layout(base)
    cloud.put(ADMIN, "shop", "_", "repository", "web", {})
    cloud.put(ADMIN, "shop", "_", "repository", "pinned", {"tag_mutability": "immutable"})
    return Registry(cloud, tmp_path / "data", base)


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    (d / "pkg").mkdir(parents=True)
    (d / "app.py").write_text("print('hi')\n")
    (d / "pkg" / "util.py").write_text("X = 1\n")
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref")
    return d


def test_base_images_are_imported(reg):
    assert reg.base_images() == ["pybase:1"]


def test_build_adds_one_deterministic_layer(reg, src):
    out = reg.build(ADMIN, "shop", "web", pack_directory(src), "pybase:1", tag="v1", cmd=["python", "app.py"],
                    env={"MODE": "prod"}, port=8080)
    assert out["image"] == f"{HOST}/shop/web@{out['digest']}"
    _, mbytes, media = reg.resolve("shop/web", "v1")
    manifest = json.loads(mbytes)
    assert media.endswith("manifest.v1+json") and len(manifest["layers"]) == 2
    layer = reg.blob_path(manifest["layers"][1]["digest"]).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(layer))) as t:
        names = t.getnames()
        assert {"app/app.py", "app/pkg/util.py"} <= set(names) and not any(".git" in n for n in names)
        assert all(m.mtime == 0 and m.uid == 0 for m in t.getmembers())
    config = json.loads(reg.blob_path(manifest["config"]["digest"]).read_bytes())
    assert config["rootfs"]["diff_ids"][-1] == _sha(gzip.decompress(layer))
    assert config["config"]["Cmd"] == ["python", "app.py"] and config["config"]["WorkingDir"] == "/app"
    assert "MODE=prod" in config["config"]["Env"] and "LANG=C" in config["config"]["Env"]
    assert config["config"]["ExposedPorts"] == {"8080/tcp": {}}
    again = reg.build(ADMIN, "shop", "web", pack_directory(src), "pybase:1", cmd=["python", "app.py"],
                      env={"MODE": "prod"}, port=8080)
    assert again["digest"] == out["digest"]  # same source + base + settings -> same image


def test_tags_move_unless_immutable(reg, src):
    a = reg.build(ADMIN, "shop", "web", pack_directory(src), "pybase:1", tag="latest")
    (src / "app.py").write_text("print('v2')\n")
    b = reg.build(ADMIN, "shop", "web", pack_directory(src), "pybase:1", tag="latest")
    assert a["digest"] != b["digest"] and reg.resolve("shop/web", "latest")[0] == b["digest"]
    reg.build(ADMIN, "shop", "pinned", pack_directory(src), "pybase:1", tag="v1")
    (src / "app.py").write_text("print('v3')\n")
    with pytest.raises(SimCloudError) as e:
        reg.build(ADMIN, "shop", "pinned", pack_directory(src), "pybase:1", tag="v1")
    assert e.value.code == "conflict"


def test_push_needs_permission_and_a_known_base(reg, src, cloud):
    with pytest.raises(SimCloudError) as e:
        reg.build(Principal("user:dev", "shop"), "shop", "web", pack_directory(src), "pybase:1")
    assert e.value.code == "access_denied"
    with pytest.raises(SimCloudError) as e:
        reg.build(ADMIN, "shop", "web", pack_directory(src), "nosuch:9")
    assert e.value.code == "not_found" and e.value.details["available"] == ["pybase:1"]


def test_pull_api_serves_manifests_and_blobs(reg, src):
    out = reg.build(ADMIN, "shop", "web", pack_directory(src), "pybase:1", tag="v1")
    c = TestClient(create_registry_app(reg))
    assert c.get("/v2/").status_code == 200
    for ref in ("v1", out["digest"]):
        r = c.get(f"/v2/shop/web/manifests/{ref}")
        assert r.status_code == 200 and r.headers["Docker-Content-Digest"] == out["digest"]
        assert _sha(r.content) == out["digest"]
    assert c.head("/v2/shop/web/manifests/v1").headers["Docker-Content-Digest"] == out["digest"]
    manifest = c.get("/v2/shop/web/manifests/v1").json()
    for desc in manifest["layers"] + [manifest["config"]]:
        assert _sha(c.get(f"/v2/shop/web/blobs/{desc['digest']}").content) == desc["digest"]
    assert c.get("/v2/library/pybase/manifests/1").status_code == 200
    assert c.get("/v2/shop/web/manifests/nope").status_code == 404
    assert c.get("/v2/shop/web/blobs/sha256:../../etc").status_code in (400, 404)


def test_build_over_the_api(cloud, reg, src, admin):
    from simcloud.api import create_app
    c = TestClient(create_app(cloud, registry=reg))
    r = c.post("/v1/projects/shop/repositories/web/build", params={"base": "pybase:1", "tag": "v1",
                                                                  "cmd": json.dumps(["python", "app.py"])},
               content=pack_directory(src), headers=admin)
    assert r.status_code == 200, r.text
    images = c.get("/v1/projects/shop/repositories/web/images", headers=admin).json()
    assert images["tags"]["v1"] == r.json()["digest"]
