import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_gen():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "gen_skill_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_reference_is_current(monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    gen = load_gen()
    for name, text in gen.render().items():
        committed = (gen.OUT / name).read_text()
        assert committed == text, f"{name} is stale: run `uv run python scripts/gen_skill_docs.py`"


def test_skill_links_resolve():
    skill = ROOT / "skills" / "simcloud" / "SKILL.md"
    text = skill.read_text()
    assert text.startswith("---\nname: simcloud\n")
    for target in ("reference/kinds.md", "reference/actions.md", "reference/errors.md", "reference/cli.md",
                   "reference/mcp-tools.md"):
        assert f"]({target})" in text and (skill.parent / target).exists()
