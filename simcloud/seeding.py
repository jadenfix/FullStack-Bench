"""World seeding: everything a task sets up before the agent starts.

The core seed (projects, resources, principals) is applied by `core.apply_seed`.
This module applies the rest, in order, once all components exist:

    issuers:        [{issuer, jwks}]                      trusted OIDC issuers
    secret_values:  [{project, env, name, value}]         initial secret versions
    kv_values:      [{project, env, store, key, value}]   initial KV entries
    sql:            [{project, env, database, statements: [...]}]  run as the database's owner role
    deployments:    [{project, env, service, source, strategy?}]
                    source is a directory inside the SimCloud container
    guard:          {...}                                  see incidents.py
    faults:         {faults: [...]}                        see faults.py (loaded last, so
                                                           setup isn't throttled and time
                                                           windows start with the episode)
"""

from pathlib import Path

from .delivery import Delivery, pack_directory
from .errors import SimCloudError
from .federation import Federation
from .identity import Principal

ADMIN = Principal("admin")


def apply_world(seed: dict, *, data, federation: Federation, delivery: Delivery | None, guard,
                databases=None) -> dict:
    report = {"deployments": []}
    for item in seed.get("sql", []):
        if databases is None:
            raise SimCloudError("unavailable", "seed has sql but this SimCloud has no managed Postgres")
        from psycopg import sql as psql
        from .databases import db_name
        name = db_name(item["project"], item["env"], item["database"])
        with databases.pg.connect(name) as c:
            c.execute(psql.SQL("SET ROLE {}").format(psql.Identifier(name + "__owner")))
            for stmt in item["statements"]:
                c.execute(stmt)
    for entry in seed.get("issuers", []):
        federation.register_issuer(ADMIN, entry["issuer"], entry["jwks"])
    for sv in seed.get("secret_values", []):
        data.add_secret_version(ADMIN, sv["project"], sv["env"], sv["name"], sv["value"])
    for kv in seed.get("kv_values", []):
        data.kv_put(ADMIN, kv["project"], kv["env"], kv["store"], kv["key"], kv["value"])
    for dep in seed.get("deployments", []):
        if delivery is None:
            raise SimCloudError("unavailable", "seed has deployments but this SimCloud has no runtime")
        archive = pack_directory(Path(dep["source"]))
        result = delivery.deploy(ADMIN, (dep["project"], dep["env"], dep["service"]), archive,
                                 dep.get("strategy", "rolling"))
        if result.get("error") or result["state"] != "ready":
            raise SimCloudError("unprocessable", f"seed deployment {dep['project']}/{dep['env']}/{dep['service']} "
                                f"failed: {result.get('error') or result['state']}")
        report["deployments"].append({**{k: dep[k] for k in ("project", "env", "service")},
                                      "release": result["release"], "digest": result["digest"]})
    if seed.get("guard") is not None and guard is not None:
        guard.configure(ADMIN, seed["guard"])
    if seed.get("faults") is not None:
        federation.cloud.faults.load(seed["faults"])
    return report
