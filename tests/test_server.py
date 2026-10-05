import stat

import yaml
from fastapi.testclient import TestClient

from simcloud.api import create_app
from simcloud.server import build, build_data

SEED = {
    "projects": [{"name": "shop", "environments": ["dev"]}],
    "resources": [
        {"project": "shop", "kind": "policy", "name": "all", "spec": {"statements": [
            {"effect": "allow", "actions": ["*"], "resources": ["*"]}]}},
        {"project": "shop", "kind": "binding", "name": "b", "spec": {"principal": "user:dev", "policies": ["all"]}},
        {"project": "shop", "env": "dev", "kind": "secret", "name": "k", "spec": {}},
    ],
}


def boot(tmp_path):
    seed = tmp_path / "seed.yaml"
    seed.write_text(yaml.safe_dump({**SEED, "principals": [
        {"name": "user:dev", "project": "shop", "token_file": str(tmp_path / "dev.token")}]}))
    db = str(tmp_path / "state" / "state.db")
    cloud = build(db, "admin-x", str(seed))
    return TestClient(create_app(cloud, build_data(cloud, db))), db


def test_seed_writes_private_token_and_state_survives_restart(tmp_path):
    client, db = boot(tmp_path)
    token_file = tmp_path / "dev.token"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    headers = {"Authorization": f"Bearer {token_file.read_text().strip()}"}
    E = "/v1/projects/shop/envs/dev"
    assert client.post(f"{E}/secret/k/versions", json={"value": "v1"}, headers=headers).status_code == 201
    for suffix in (".kms", ".urlkey"):
        assert stat.S_IMODE((tmp_path / "state" / f"state{suffix}").stat().st_mode) == 0o600

    # restart: same DB and key files; the seed is not re-applied, the token still works
    client2, _ = boot(tmp_path)
    assert client2.post(f"{E}/secret/k/access", json={}, headers=headers).json()["value"] == "v1"
    assert client2.get("/admin/v1/audit/verify", headers={"Authorization": "Bearer admin-x"}).json()["intact"]
