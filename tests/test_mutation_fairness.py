"""Exercise alternate valid cancellation and MCP behavior through the real probes."""

import copy
import threading
import pytest
from fsbench.mutation_scenarios import Scenarios


@pytest.mark.parametrize("wrong_window", [False, True])
def test_cancellation_does_not_prescribe_its_response_body_or_journal_kind(
    monkeypatch, wrong_window
):
    scenario = Scenarios("http://orders", "", "", "", lambda: None)
    initial = {"row": ["scheduled", "start", "end"], "events": 0}
    after = {
        "row": ["cancelled", "start", "wrong" if wrong_window else "end"],
        "events": 0,
    }
    cancelled = threading.Event()
    scenario.order = lambda: "id"
    scenario.state = lambda order: copy.deepcopy(
        after if cancelled.is_set() else initial
    )

    def request(*args, **kwargs):
        cancelled.set()
        return 204, None

    scenario.request = request
    scenario.patch = lambda *args: (409, None)
    scenario.wait_blocked = lambda *args, **kwargs: cancelled.wait(1)

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

    monkeypatch.setattr(
        "fsbench.mutation_scenarios.psycopg.connect", lambda *args, **kwargs: Lock()
    )
    if wrong_window:
        with pytest.raises(AssertionError, match="window changed"):
            scenario.cancellation_race()
    else:
        scenario.cancellation_race()


@pytest.mark.parametrize("mutate_replay", [False, True])
def test_mcp_replay_compares_application_payload_and_checks_persisted_state(
    mutate_replay,
):
    scenario = Scenarios("http://orders", "", "", "", lambda: None, "http://mcp")
    scenario.order = lambda: "id"
    receipts = {}
    state = {"hour": 8}
    count = 0
    scenario.state = lambda order: dict(state)

    def assert_window(order, hour):
        assert state["hour"] == hour

    scenario.assert_window = assert_window

    def request(method, path, body, **kwargs):
        nonlocal count
        count += 1
        args = body["params"]["arguments"]
        key = args["idempotency_key"]
        if key not in receipts:
            receipts[key] = {
                "order_id": "id",
                "delivery_window": {
                    "start": args["window_start"],
                    "end": args["window_end"],
                },
            }
            state["hour"] = int(args["window_start"][11:13])
        elif mutate_replay:
            state["hour"] = 10
        payload = copy.deepcopy(receipts[key])
        if count > 2:
            payload["delivery_window"] = {
                field: value.replace("Z", "+00:00")
                for field, value in payload["delivery_window"].items()
            }
        return 200, {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": payload,
                "content": [{"type": "text", "text": "request diagnostic"}],
                "_meta": {"trace": count},
            },
        }

    scenario.request = request
    if mutate_replay:
        with pytest.raises(AssertionError, match="re-applied"):
            scenario.concierge_replay()
    else:
        scenario.concierge_replay()
