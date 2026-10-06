"""HTTP error formatting must not add requirements to the mutation contract."""

import io
import json
import urllib.error
import urllib.request

import pytest

from fsbench.mutation_scenarios import Scenarios


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (409, b"", None),
        (409, b"conflict", None),
        (400, b"cancelled", None),
        (409, b'{"error":"conflict"}', {"error": "conflict"}),
        (500, b'"journal rejected"', "journal rejected"),
        (503, b"null", None),
    ],
)
def test_declared_status_allows_unspecified_error_format(
    monkeypatch, status, body, expected
):
    def respond(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, status, "error", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    scenarios = Scenarios("http://service", "", "", "", lambda: None)
    assert scenarios.request("PATCH", "/delivery", {}) == (status, expected)


@pytest.mark.parametrize("status", [200, 201, 500, 503])
def test_success_and_declared_json_server_error_reject_malformed_json(
    monkeypatch, status
):
    class Response(io.BytesIO):
        pass

    def respond(req, timeout):
        result = Response(b"not JSON")
        result.status = status
        return result

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    with pytest.raises(json.JSONDecodeError):
        Scenarios("http://service", "", "", "", lambda: None).request(
            "PATCH", "/delivery", {}
        )


@pytest.mark.parametrize("status,body", [(200, b"cancelled"), (204, b"")])
def test_cancellation_can_succeed_without_a_prescribed_json_body(
    monkeypatch, status, body
):
    class Response(io.BytesIO):
        pass

    def respond(req, timeout):
        result = Response(body)
        result.status = status
        return result

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    assert Scenarios("http://service", "", "", "", lambda: None).request(
        "POST", "/orders/id/cancel", {}, allow_non_json_success=True
    ) == (status, None)


def test_equivalent_delivery_instants_compare_equal_but_other_response_fields_do_not():
    original = {
        "order_id": "id",
        "delivery_window": {
            "start": "2020-01-01T10:00:00Z",
            "end": "2020-01-01T11:00:00Z",
        },
        "slot": "AM",
    }
    replay = {
        "order_id": "id",
        "delivery_window": {
            "start": "2020-01-01T11:00:00+01:00",
            "end": "2020-01-01T11:00:00+00:00",
        },
        "slot": "AM",
    }
    assert Scenarios.delivery_payload(original) == Scenarios.delivery_payload(replay)
    assert original["delivery_window"]["start"] == "2020-01-01T10:00:00Z"
    assert Scenarios.delivery_payload(original) != Scenarios.delivery_payload(
        {**replay, "slot": "PM"}
    )


def test_arbitrary_json_server_error_does_not_receive_success_schema_validation():
    scenario = Scenarios("http://service", "", "", "", lambda: None)
    body = {"delivery_window": {"diagnostic": "journal failure"}}
    scenario.request = lambda *args, **kwargs: (500, body)
    assert scenario.patch("id", scenario.body(10)) == (500, body)
