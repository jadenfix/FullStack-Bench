"""The part of scripts/render_receipts.py that reads another repository's source: Rusty's context
handling at the pinned commit. The rest needs the pinned binary and receipts and is checked by
running the script itself."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("render_receipts", ROOT / "scripts" / "render_receipts.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

CONTEXT = '''pub const REPLY_RESERVE: usize = 16_000;
pub fn estimate_one(m: &Value) -> usize {
    let mut chars = 16;
    chars / 4
}
pub fn window() -> usize {
    std::env::var("RUSTY_CONTEXT_TOKENS").ok().and_then(|v| v.parse().ok()).unwrap_or(128_000)
}
'''
AGENT = '''        if context::estimate(&self.history) > budget * 6 / 10 {
            let saved = context::elide_old_tool_outputs(&mut self.history);
        }
        if context::estimate(&self.history) > budget * 8 / 10 {
'''


def source(tmp_path, context=CONTEXT, agent=AGENT):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/context.rs").write_text(context)
    (tmp_path / "src/agent.rs").write_text(agent)
    return tmp_path


def test_context_handling_is_read_from_the_pinned_source(tmp_path):
    assert rr.context_handling(source(tmp_path)) == {
        "window": 128_000, "reserve": 16_000, "per_token": 4, "elide": 60, "compact": 80}


def test_a_source_that_moved_is_refused_not_guessed(tmp_path):
    with pytest.raises(SystemExit, match="not where this script expects"):
        rr.context_handling(source(tmp_path, agent=AGENT.replace("budget * 8 / 10", "budget.compact_at()")))
