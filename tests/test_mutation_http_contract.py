"""HTTP error formatting must not add requirements to the mutation contract."""
import io
import json
import urllib.error
import urllib.request

import pytest

from fsbench.mutation_scenarios import Scenarios


@pytest.mark.parametrize('status,body,expected', [(409, b'', None), (409, b'conflict', None),
    (400, b'cancelled', None), (409, b'{"error":"conflict"}', {'error': 'conflict'}),
    (500, b'"journal rejected"', 'journal rejected'), (503, b'null', None)])
def test_declared_status_allows_unspecified_error_format(monkeypatch, status, body, expected):
    def respond(req, timeout):
        raise urllib.error.HTTPError(req.full_url, status, 'error', {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, 'urlopen', respond)
    scenarios = Scenarios('http://service', '', '', '', lambda: None)
    assert scenarios.request('PATCH', '/delivery', {}) == (status, expected)


@pytest.mark.parametrize('status', [200, 201, 500, 503])
def test_success_and_declared_json_server_error_reject_malformed_json(monkeypatch, status):
    class Response(io.BytesIO):
        pass

    def respond(req, timeout):
        result = Response(b'not JSON')
        result.status = status
        return result

    monkeypatch.setattr(urllib.request, 'urlopen', respond)
    with pytest.raises(json.JSONDecodeError):
        Scenarios('http://service', '', '', '', lambda: None).request('PATCH', '/delivery', {})
