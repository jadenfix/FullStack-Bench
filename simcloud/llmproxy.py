"""The LLM proxy: the only way out of an offline task's network.

Offline tasks put the agent and SimCloud on an `internal` compose network with no route to the
internet. This proxy sits on that network and on an outside one, and forwards requests to exactly
one upstream (the model endpoint). Everything else is unreachable.

    LLMPROXY_UPSTREAM=https://integrate.api.nvidia.com python -m simcloud.llmproxy

The agent points its OpenAI-compatible base URL at http://llm-proxy:8088/v1. Requests keep their
method, path, query, body and Authorization header; responses stream back. The proxy logs method,
path, status and timing only, never bodies or credentials.
"""

import json
import os
import sys
import time
from urllib.parse import urlsplit

import httpx

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


class Proxy:
    def __init__(self, upstream: str, allowed_prefixes: tuple[str, ...] = ("/v1/",), transport=None, log=None):
        u = urlsplit(upstream)
        if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/"):
            raise ValueError("upstream must be a scheme://host[:port] URL")
        self.upstream = f"{u.scheme}://{u.netloc}"
        self.allowed = allowed_prefixes
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(3600, connect=15), transport=transport)
        self.log = log or (lambda rec: print(json.dumps(rec), flush=True))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await self.client.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path = scope["path"]
        start = time.perf_counter()
        if path == "/healthz":
            return await _respond(send, 200, b"ok")
        if not path.startswith(self.allowed):
            self.log({"event": "refused", "method": scope["method"], "path": path})
            return await _respond(send, 403, b"only the model API is reachable from this network")
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.decode().lower() not in HOP]
        qs = scope.get("query_string", b"").decode()
        url = self.upstream + path + (f"?{qs}" if qs else "")
        try:
            req = self.client.build_request(scope["method"], url, headers=headers, content=body)
            resp = await self.client.send(req, stream=True)
        except httpx.HTTPError as e:
            self.log({"event": "upstream_error", "path": path, "error": type(e).__name__})
            return await _respond(send, 502, b"upstream unreachable")
        out = [(k.encode(), v.encode()) for k, v in resp.headers.items() if k.lower() not in HOP]
        await send({"type": "http.response.start", "status": resp.status_code, "headers": out})
        size = 0
        try:
            async for chunk in resp.aiter_raw():
                size += len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            await resp.aclose()
        await send({"type": "http.response.body", "body": b""})
        self.log({"event": "proxied", "method": scope["method"], "path": path, "status": resp.status_code,
                  "bytes": size, "ms": round((time.perf_counter() - start) * 1000)})


async def _respond(send, status: int, body: bytes):
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


def main() -> int:
    import uvicorn
    upstream = os.environ.get("LLMPROXY_UPSTREAM")
    if not upstream:
        print("LLMPROXY_UPSTREAM must be set", file=sys.stderr)
        return 2
    uvicorn.run(Proxy(upstream), host="0.0.0.0", port=int(os.environ.get("LLMPROXY_PORT", "8088")), log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
