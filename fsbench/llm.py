"""A small OpenAI-compatible chat client for authoring and QA models.

Keys come from .env (NVIDIA_API_KEY / NVIDIA_API_BASE), loaded here, never passed on a command
line or printed. Every call is bounded (timeout, max tokens, attempts) and its usage is returned
for the cost ledger; the NVIDIA endpoint reports tokens but no cost.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


@dataclass
class Reply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    finish_reason: str | None


class LLMError(RuntimeError):
    pass


class Client:
    def __init__(self, base: str | None = None, key: str | None = None, timeout: float = 900):
        env = load_env()
        self.base = (base or env.get("NVIDIA_API_BASE") or "").rstrip("/")
        self._key = key or env.get("NVIDIA_API_KEY")
        self.timeout = timeout
        if not self.base or not self._key:
            raise LLMError("NVIDIA_API_BASE and NVIDIA_API_KEY must be set in .env")

    def chat(self, model: str, messages: list[dict], max_tokens: int = 16000, temperature: float = 0.4,
             attempts: int = 3, extra: dict | None = None) -> Reply:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                **(extra or {})}
        last = None
        for i in range(attempts):
            start = time.monotonic()
            req = urllib.request.Request(f"{self.base}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {self._key}",
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                last = LLMError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
                if e.code < 500 and e.code != 429:
                    raise last
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = LLMError(f"{type(e).__name__}: {e}")
            else:
                choice = data["choices"][0]
                text = (choice["message"].get("content") or "").strip()
                usage = data.get("usage") or {}
                if text:
                    return Reply(text, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                 round(time.monotonic() - start, 1), choice.get("finish_reason"))
                last = LLMError(f"empty reply (finish {choice.get('finish_reason')})")
            time.sleep(min(60, 5 * 2 ** i))
        raise last or LLMError("no reply")
