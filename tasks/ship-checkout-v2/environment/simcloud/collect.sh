#!/bin/sh
# Runs inside the SimCloud sidecar after the agent phase (verifier.collect). The agent can't
# reach this container's filesystem or the operator token.
exec python - <<'EOF'
import json, os, urllib.error, urllib.request

os.makedirs("/evidence", exist_ok=True)
admin = {"Authorization": "Bearer " + os.environ["SIMCLOUD_ADMIN_TOKEN"]}


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # unreachable, timeout
        return 0, repr(e)


status, body = get("http://127.0.0.1:7400/admin/v1/evidence", admin)
with open("/evidence/evidence.json", "w") as f:
    f.write(body if status == 200 else json.dumps({"error": f"evidence unavailable ({status})"}))

# What a customer sees right now: five quotes through the load balancer.
with open("/evidence/probe.jsonl", "w") as f:
    for _ in range(5):
        code, text = get("http://127.0.0.1:7480/_svc/shop/prod/web/checkout/quote?cart=demo")
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = text[:300]
        f.write(json.dumps({"status": code, "body": parsed}) + "\n")
EOF
