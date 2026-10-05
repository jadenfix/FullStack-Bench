import io
import json
from types import SimpleNamespace

import pytest

from simcloud.cli import Client
from simcloud.mcp_server import TOOLS, Server, serve


@pytest.fixture
def mcp(client, cloud, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMCLOUD_TOKEN", cloud.issued["user:dev"])
    return Server(Client(SimpleNamespace(url="http://testserver", project=None), http=client))


def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_initialize_and_list(mcp):
    init = rpc(mcp, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert rpc(mcp, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    tools = rpc(mcp, "tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == set(TOOLS)
    put = next(t for t in tools if t["name"] == "put_resource")
    assert put["inputSchema"]["required"] == ["env", "kind", "name", "spec"]
    assert "optional" not in json.dumps(tools)
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def call(mcp, tool, **args):
    return rpc(mcp, "tools/call", {"name": tool, "arguments": args})["result"]


def test_tools_drive_simcloud(mcp):
    assert json.loads(call(mcp, "whoami")["content"][0]["text"])["principal"] == "user:dev"
    r = call(mcp, "put_resource", env="dev", kind="queue", name="jobs", spec={"max_receives": 3})
    assert not r["isError"] and json.loads(r["content"][0]["text"])["version"] == 1
    assert not call(mcp, "queue_send", env="dev", queue="jobs", body={"n": 1})["isError"]
    msgs = json.loads(call(mcp, "queue_receive", env="dev", queue="jobs")["content"][0]["text"])["messages"]
    assert msgs[0]["body"] == {"n": 1}
    plan = call(mcp, "plan", document={"project": "shop", "environments": {"dev": {"kv": {"a": {}}}}})
    assert json.loads(plan["content"][0]["text"])["summary"]["create"] == 1


def test_errors_are_tool_errors_not_crashes(mcp):
    r = call(mcp, "put_resource", env="prod", kind="kv", name="x", spec={})
    assert r["isError"] and "access_denied" in r["content"][0]["text"]
    assert call(mcp, "nope")["isError"]
    assert call(mcp, "get_resource", env="dev")["isError"]  # missing arguments
    assert rpc(mcp, "bogus/method")["error"]["code"] == -32601


def test_stdio_transport(mcp):
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n\n'
                        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    out = io.StringIO()
    serve(stdin, out, client=mcp.client)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert lines[1]["error"]["code"] == -32700 and len(lines) == 2
