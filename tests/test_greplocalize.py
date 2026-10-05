from pathlib import Path

from fsbench.greplocalize import brief_tokens, localise


def test_tokens_from_a_brief():
    toks = brief_tokens('Call `GET /checkout/quote?cart=demo`, set PAYMENTS_SIGNING_KEY and "engine": "v2" via checkout.engine')
    assert {"/checkout/quote", "PAYMENTS_SIGNING_KEY", "checkout.engine", "GET /checkout/quote?cart=demo"} <= set(toks)


def _task(tmp_path, brief, files, causal, hidden):
    (tmp_path / "environment" / "repo").mkdir(parents=True)
    for name, text in files.items():
        p = tmp_path / "environment" / "repo" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "instruction.md").write_text(brief)
    (tmp_path / "task.toml").write_text(f"[metadata]\ncausal_path = {causal!r}\nhidden_literals = {hidden!r}\n")
    return tmp_path


def test_shallow_task_is_flagged(tmp_path):
    t = _task(tmp_path, "Fix the crash when `ORDER_TIMEOUT` is unset.", {
        "app.py": "import os\nT = int(os.environ['ORDER_TIMEOUT'])\n", "other.py": "x = 1\n"}, ["app.py"], [])
    assert localise(t).shallow


def test_hidden_literal_in_code_is_flagged(tmp_path):
    t = _task(tmp_path, "Ship it.", {"a.py": "KEY = 'PAYMENTS_SIGNING_KEY'\n", "b.py": "y = 2\n"},
              ["b.py"], ["PAYMENTS_SIGNING_KEY"])
    r = localise(t)
    assert r.hidden_literal_hits == {"PAYMENTS_SIGNING_KEY": ["a.py"]} and r.shallow
