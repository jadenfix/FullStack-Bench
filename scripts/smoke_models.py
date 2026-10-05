"""One bounded call per candidate authoring model on the NVIDIA endpoint.

Reads keys from .env (never from the command line, never printed). Prints, per
model: whether the call worked, latency, token usage and whether the reply
followed a tiny structured-output instruction. No retries; 120 s per call.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CANDIDATES = [
    "moonshotai/kimi-k3",
    "z-ai/glm-5.3",
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3-ultra-550b-a55b",
]
PROMPT = (
    "Reply with only a JSON object, no prose and no code fences: "
    '{"option": "<A or B>", "reason": "<under 20 words>"}. '
    "A browser app with its own server must keep an XSS from stealing a "
    "long-lived credential. A: store the session in an HttpOnly cookie. "
    "B: store a JWT in localStorage."
)
TIMEOUT_SEC = 120
MAX_TOKENS = 2048


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def call(base: str, key: str, model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"}
    except Exception as e:  # timeouts, DNS, TLS
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    elapsed = time.monotonic() - start
    choice = data["choices"][0]
    content = (choice["message"].get("content") or "").strip()
    try:
        parsed = json.loads(content)
        followed = parsed.get("option") == "A"
    except json.JSONDecodeError:
        followed = False
    return {
        "ok": True,
        "latency_s": round(elapsed, 1),
        "usage": data.get("usage"),
        "finish_reason": choice.get("finish_reason"),
        "message_fields": sorted(choice["message"]),
        "valid_json_and_correct": followed,
        "reply": content[:160],
    }


def main() -> int:
    env = load_env(Path(__file__).resolve().parent.parent / ".env")
    base, key = env.get("NVIDIA_API_BASE"), env.get("NVIDIA_API_KEY")
    if not base or not key:
        print("NVIDIA_API_BASE and NVIDIA_API_KEY must be set in .env", file=sys.stderr)
        return 2
    models = sys.argv[1:] or CANDIDATES
    results = {m: call(base, key, m) for m in models}
    print(json.dumps(results, indent=2))
    return 0 if any(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
