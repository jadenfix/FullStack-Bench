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
    def __init__(self, base: str | None = None, key: str | None = None, timeout: float = 300,
                 max_seconds: float = 2700):
        env = load_env()
        self.base = (base or env.get("NVIDIA_API_BASE") or "").rstrip("/")
        self._key = key or env.get("NVIDIA_API_KEY")
        self.timeout = timeout  # per socket read
        self.max_seconds = max_seconds  # per reply, wall clock: a trickling stream can't hang a run
        if not self.base or not self._key:
            raise LLMError("NVIDIA_API_BASE and NVIDIA_API_KEY must be set in .env")

    def chat(self, model: str, messages: list[dict], max_tokens: int = 16000, temperature: float = 0.4,
             attempts: int = 3, extra: dict | None = None, stream: bool = True) -> Reply:
        """Streaming by default: long generations outlive gateway timeouts on non-streamed requests."""
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                **({"stream": True, "stream_options": {"include_usage": True}} if stream else {}), **(extra or {})}
        last = None
        for i in range(attempts):
            start = time.monotonic()
            req = urllib.request.Request(f"{self.base}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {self._key}",
                                                  "Content-Type": "application/json",
                                                  "Accept": "text/event-stream" if stream else "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text, usage, finish = (self._read_stream(r, start + self.max_seconds) if stream
                                           else self._read_json(r))
            except urllib.error.HTTPError as e:
                last = LLMError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
                if e.code < 500 and e.code != 429:
                    raise last
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                last = LLMError(f"{type(e).__name__}: {e}")
            else:
                if text:
                    return Reply(text, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                 round(time.monotonic() - start, 1), finish)
                last = LLMError(f"empty reply (finish {finish})")
            time.sleep(min(60, 5 * 2 ** i))
        raise last or LLMError("no reply")

    @staticmethod
    def _read_json(r):
        data = json.load(r)
        choice = data["choices"][0]
        return (choice["message"].get("content") or "").strip(), data.get("usage") or {}, choice.get("finish_reason")

    @staticmethod
    def _read_stream(r, deadline: float | None = None):
        parts, usage, finish = [], {}, None
        for raw in r:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"reply not finished within the time limit ({len(''.join(parts))} chars so far)")
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        return "".join(parts).strip(), usage, finish
