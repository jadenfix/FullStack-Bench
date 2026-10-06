import json
import urllib.error
import urllib.request

from . import settings


class LedgerError(Exception):
    def __init__(self, status, body, retry_after=None):
        super().__init__(f"ledger {status}: {body}")
        self.status, self.body, self.retry_after = status, body, retry_after


def _call(method, path, body=None):
    req = urllib.request.Request(settings.LEDGER_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=settings.LEDGER_TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"raw": raw[:200].decode(errors="replace")}
        raise LedgerError(e.code, data, e.headers.get("retry-after"))
    except (urllib.error.URLError, OSError) as e:
        raise LedgerError(0, {"error": repr(e)[:200]})


def put(entry_id, booking):
    return _call("POST", "/entries", {"id": entry_id, "booking": booking})


def get(entry_id):
    return _call("GET", f"/entries/{entry_id}")


def healthy():
    try:
        return _call("GET", "/healthz")[0] == 200
    except LedgerError:
        return False
