import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_variant", ROOT / "scripts" / "make_variant.py")
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)


def test_offline_variant_isolates_everything_but_the_proxy(tmp_path):
    out = mv.make_offline(ROOT / "tasks" / "ship-checkout-v2", tmp_path)
    compose = yaml.safe_load((out / "environment" / "docker-compose.yaml").read_text())
    assert compose["networks"]["inner"] == {"internal": True}
    assert compose["services"]["main"]["networks"] == ["inner"]
    assert compose["services"]["simcloud"]["networks"] == ["inner"]
    assert compose["services"]["llm-proxy"]["networks"] == ["inner", "outer"]
    assert compose["services"]["llm-proxy"]["environment"]["LLMPROXY_UPSTREAM"].startswith("https://")
    assert "uv tool install" in (out / "environment" / "Dockerfile").read_text()
    assert "EGRESS_BLOCKED" in (out / "solution" / "solve.sh").read_text()
    assert 'name = "fullstack-bench-offline/' in (out / "task.toml").read_text()
    assert not (out / "wrong_solutions").exists()
