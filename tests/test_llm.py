import io
import json

from fsbench.llm import Client


def test_reads_an_sse_stream_with_usage():
    events = [{"choices": [{"delta": {"content": "=== FILE: a ==="}}]},
              {"choices": [{"delta": {"content": "\nx\n"}, "finish_reason": "stop"}]},
              {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
    raw = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
    text, usage, finish = Client._read_stream(io.BytesIO(raw))
    assert text == "=== FILE: a ===\nx" and usage["completion_tokens"] == 5 and finish == "stop"
