# settings. env wins over defaults. (SETTINGS_FILE support was removed in 2026.06, see CHANGES)
import os


def _range(v, default):
    try:
        lo, hi = (int(x) for x in (v or default).split("-"))
        return lo, hi
    except ValueError:
        return tuple(int(x) for x in default.split("-"))


PORT = int(os.environ.get("PORT", "8080"))
LEDGER_URL = os.environ.get("LEDGER_URL", "http://ledger:7000").rstrip("/")
# how long holding the class-slot reservation takes, ms. it's the schedules service in real life;
# here it's simulated because schedules got folded into us (PULSE-88).
HOLD_MS = _range(os.environ.get("HOLD_MS"), "700-1500")
LEDGER_TIMEOUT = float(os.environ.get("LEDGER_TIMEOUT", "4"))
MAX_PARTY = int(os.environ.get("MAX_PARTY", "4"))
TELEMETRY_FLUSH = os.environ.get("TELEMETRY_FLUSH", "1") != "0"
