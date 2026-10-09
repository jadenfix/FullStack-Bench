"""The runner end to end at zero cost: real planner commands, runner, gateways and ledger; a fake
`harbor` that behaves like one trial, and a fake provider behind the gateways."""

import hashlib
import json
import socket
import sys

import httpx
import pytest

from fsbench import experiment, runner

FAKE_HARBOR = '''#!{python}
import json, os, pathlib, sys, urllib.request
from dirhash import dirhash
a = sys.argv[1:]
opt = lambda n: a[a.index(n) + 1]
out, job, task = pathlib.Path(opt("-o")), opt("--job-name"), opt("-p")
episode, attempt = job.rsplit("--a", 1)
behaviour = json.loads(pathlib.Path("behaviour.json").read_text()).get(episode, ["ok"])
mode = behaviour[min(int(attempt), len(behaviour)) - 1]
if mode == "crash":
    sys.exit(3)
token = os.environ.get("NVIDIA_API_KEY") or os.environ["OPENAI_API_KEY"]
base = os.environ.get("RUSTY_BASE_URL") or os.environ["OPENAI_BASE_URL"]
body = json.dumps({{"model": "nvidia/test-model", "messages": [{{"role": "user", "content": "hi"}}],
                   "max_tokens": 64}}).encode()
req = urllib.request.Request(base + "/chat/completions", body,
                             {{"authorization": "Bearer " + token, "content-type": "application/json"}})
urllib.request.urlopen(req).read()
leaked = sorted(k for k, v in os.environ.items() if v in ("real-1", "real-2", "do-not-pass"))
exc = {{"provider": {{"exception_type": "ApiRateLimitError", "exception_message": "429"}},
       "config": {{"exception_type": "RustyConfigurationError", "exception_message": "memory=x"}}}}.get(mode)
trial = out / job / (pathlib.Path(task).name + "__abc")
trial.mkdir(parents=True)
(trial / "result.json").write_text(json.dumps({{
    "trial_name": trial.name, "task_checksum": dirhash(task, "sha256"), "exception_info": exc,
    "verifier_result": None if exc else {{"rewards": {{"reward": 1.0 if "rusty" in job else 0.0, "practices": 0.5}}}},
    "agent_result": {{"n_input_tokens": 10, "n_output_tokens": 5,
                     "metadata": {{"leaked": leaked, "pythonpath": os.environ.get("PYTHONPATH")}}}},
    "agent_execution": {{"started_at": "2026-10-09T00:00:00", "finished_at": "2026-10-09T00:01:40"}}}}))
'''


class FakeDocker:
    def __init__(self, images=None, running=()):
        self.images = {"fullstack-bench/simcloud:dev": "sha256:base"} if images is None else images
        self._running = list(running)

    def image_id(self, name):
        return self.images.get(name)

    def running(self):
        return self._running


def free_port_pair():
    while True:
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            port = a.getsockname()[1]
        with socket.socket() as b:
            try:
                b.bind(("127.0.0.1", port + 1))
                return port
            except OSError:
                continue


@pytest.fixture
def cohort(tmp_path):
    fsb = tmp_path / "fsb"
    task = fsb / "tasks" / "demo"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM fullstack-bench/simcloud:dev\n")
    (task / "task.toml").write_text("[environment]\ncpus = 2\nmemory_mb = 4096\nbuild_timeout_sec = 60.0\n"
                                    "[agent]\ntimeout_sec = 60.0\n[verifier]\ntimeout_sec = 60.0\n")
    (fsb / "behaviour.json").write_text("{}")
    binary = tmp_path / "rusty-bin"
    binary.write_bytes(b"elf")
    harbor = tmp_path / "harbor"
    harbor.write_text(FAKE_HARBOR.format(python=sys.executable))
    harbor.chmod(0o755)
    m = {
        "name": "e2e", "model": {"id": "nvidia/test-model", "revision": "unknown"},
        "envelope": {"calls": 5, "input_tokens": 100_000, "output_tokens": 10_000, "wall_seconds": 600},
        "inference": {"temperature": 0.2, "top_p": 1.0, "max_reply": 1024, "reasoning_effort": None},
        "runtime": {"cpus_reserved": 2, "cpus_limit": 2, "memory_reserved_mb": 4096, "memory_limit_mb": 4096,
                    "max_concurrent_trials": 2, "cache": "warm", "ordering": "counterbalanced", "order_seed": 1,
                    "key_slots": [1, 2]},
        "seeds": [0, 1],
        "tasks": [{"name": "demo", "checksum": runner.task_checksum(task), "public_check": None}],
        "tracks": [{"name": "rusty-baseline", "harness": "rusty", "binary": str(binary),
                    "binary_sha256": hashlib.sha256(b"elf").hexdigest(), "execution": "standard", "memory": "off",
                    "agents": "off", "verify": False},
                   {"name": "mini", "harness": "mini-swe-agent", "version": "2.4.6", "config_file": "c.yaml"}],
    }
    episodes = [{**e, "command": experiment.harbor_command(m, next(t for t in m["tracks"] if t["name"] == e["track"]),
                                                          m["tasks"][0], e["episode"], None)}
                for e in experiment.schedule(m)]
    plan = {"manifest_sha256": runner.manifest_sha256(m), "executed": False, "episodes": episodes}
    upstream = []

    def provider(request):
        upstream.append(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    env = {"PATH": "/usr/bin:/bin", "NVIDIA_API_KEY": "real-1", "NVIDIA_API_KEY_2": "real-2",
           "MY_SECRET_TOKEN": "do-not-pass", "HOME": str(tmp_path)}

    def make(**kw):
        opts = {"fsb_dir": fsb, "out": tmp_path / "out", "max_total_calls": 100, "docker": FakeDocker(),
                "harbor": str(harbor), "env": env} | kw
        return runner.Runner(plan, m, **opts)

    def gateways():
        return runner.Gateways({1: "real-1", 2: "real-2"}, "127.0.0.1", free_port_pair(),
                               transport=httpx.MockTransport(provider))

    return {"m": m, "plan": plan, "fsb": fsb, "make": make, "gateways": gateways, "upstream": upstream,
            "behaviour": fsb / "behaviour.json", "out": tmp_path / "out"}


def attempts(r):
    return [x for x in r.records() if x["kind"] == "attempt"]


def test_a_cohort_runs_end_to_end_through_the_gateways(cohort):
    first = cohort["plan"]["episodes"][0]["episode"]
    cohort["behaviour"].write_text(json.dumps({first: ["provider", "ok"]}))
    r = cohort["make"]()
    assert r.preflight() == []
    with cohort["gateways"]() as gw:
        assert r.run(gw) == "complete"
    done = attempts(r)
    assert len(done) == 5, "four episodes, one of them replaced once"
    replaced = [x for x in done if x["episode"] == first]
    assert [(x["attempt"], x["status"]) for x in replaced] == [(1, "provider_error"), (2, "scored")]
    assert replaced[1]["job"] == first + "--a2"
    for x in done:
        assert x["gateway"]["admitted_calls"] == 1 and x["gateway"]["known_prompt_tokens"] == 12
        if x["status"] == "scored":
            assert x["agent_metadata"]["leaked"] == [], "no provider key or other secret reached the solver"
            assert x["agent_metadata"]["pythonpath"] == str(cohort["fsb"])
            assert x["rewards"]["reward"] == (1.0 if x["harness"] == "rusty" else 0.0)
            assert x["throttle_confounded"] is False
    # Each attempt's call went upstream on its planned key, through that slot's gateway only.
    slots = [x["key_slot"] for x in sorted(done, key=lambda x: (x["wave"], x["started_at"]))]
    assert sorted(cohort["upstream"]) == sorted(f"real-{s}" for s in slots)
    stop = [x for x in r.records() if x["kind"] == "stop"][-1]
    assert stop["reason"] == "complete" and stop["admitted_calls"] == 5
    # A second invocation finds nothing to do and spends nothing.
    again = cohort["make"]()
    assert again.pending() == []
    with cohort["gateways"]() as gw:
        assert again.run(gw) == "complete"
    assert len(attempts(again)) == 5 and len(cohort["upstream"]) == 5


def test_an_attempt_the_host_never_finished_is_recorded_and_replaced(cohort):
    r = cohort["make"]()
    orphan = cohort["plan"]["episodes"][1]["episode"]
    (cohort["out"] / "jobs" / f"{orphan}--a1").mkdir(parents=True)
    with cohort["gateways"]() as gw:
        assert r.run(gw) == "complete"
    mine = [x for x in attempts(r) if x["episode"] == orphan]
    assert [(x["attempt"], x["status"]) for x in mine] == [(1, "interrupted"), (2, "scored")]


def test_replacements_are_capped_and_a_crash_without_a_trial_is_not_scored(cohort):
    first = cohort["plan"]["episodes"][0]["episode"]
    cohort["behaviour"].write_text(json.dumps({first: ["crash", "crash", "crash"]}))
    r = cohort["make"](max_attempts=2)
    with cohort["gateways"]() as gw:
        assert r.run(gw) == "complete"
    mine = [x for x in attempts(r) if x["episode"] == first]
    assert [x["status"] for x in mine] == ["no_trial", "no_trial"], "two attempts, then the episode stays missing"


def test_a_configuration_error_stops_the_run(cohort):
    first = cohort["plan"]["episodes"][0]["episode"]
    cohort["behaviour"].write_text(json.dumps({first: ["config"]}))
    r = cohort["make"]()
    with cohort["gateways"]() as gw:
        assert r.run(gw).startswith("configuration_error")
    assert {x["wave"] for x in attempts(r)} == {0}, "no later wave ran"


@pytest.mark.parametrize("change, message", [
    (lambda c: c["m"]["runtime"].update(cache="cold"), "declare runtime.cache"),
    (lambda c: c["m"]["tasks"][0].update(checksum="0" * 64), "content differs"),
    (lambda c: c["m"]["runtime"].update(memory_limit_mb=8192), "container limits"),
    (lambda c: c["m"]["tracks"][0].update(binary_sha256="0" * 64), "does not match binary_sha256"),
    (lambda c: c["plan"].update(executed=True), "already executed"),
])
def test_preflight_refuses_what_it_cannot_honour(cohort, change, message):
    change(cohort)
    if "manifest_sha256" in cohort["plan"] and message != "already executed":
        cohort["plan"]["manifest_sha256"] = runner.manifest_sha256(cohort["m"])
    assert any(message in e for e in cohort["make"]().preflight())


def test_preflight_refuses_a_missing_base_a_busy_host_and_an_unapproved_budget(cohort):
    assert any("not built locally" in e for e in cohort["make"](docker=FakeDocker(images={})).preflight())
    assert any("already running" in e for e in cohort["make"](docker=FakeDocker(running=["abc"])).preflight())
    # 4 episodes x 5 calls x 2 attempts = 40
    assert any("exceeds --max-total-calls 39" in e for e in cohort["make"](max_total_calls=39).preflight())
    assert cohort["make"](max_total_calls=40).preflight() == []
    tampered = dict(cohort["plan"], manifest_sha256="x")
    assert any("not made from this manifest" in e for e in runner.Runner(
        tampered, cohort["m"], fsb_dir=cohort["fsb"], out=cohort["out"], max_total_calls=100,
        docker=FakeDocker()).preflight())


def test_solver_environment_keeps_only_the_trial_token():
    base = {"PATH": "/bin", "NVIDIA_API_KEY": "real", "NVIDIA_API_KEY_2": "real2", "OPENAI_API_KEY": "o",
            "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "a", "RUSTY_BASE_URL": "old", "SSL_CERT_FILE": "/ca"}
    rusty = runner.solver_env(base, "rusty", "tok", "http://gw/v1", runner.Path("/fsb"))
    assert rusty == {"PATH": "/bin", "SSL_CERT_FILE": "/ca", "PYTHONPATH": "/fsb", "NVIDIA_API_KEY": "tok",
                     "RUSTY_BASE_URL": "http://gw/v1"}
    mini = runner.solver_env(base, "mini-swe-agent", "tok", "http://gw/v1", runner.Path("/fsb"))
    assert mini["OPENAI_API_KEY"] == "tok" and "NVIDIA_API_KEY" not in mini


def test_keys_come_from_distinct_slots():
    assert runner.provider_keys([1, 2], {"NVIDIA_API_KEY": "a", "NVIDIA_API_KEY_2": "b"}) == {1: "a", 2: "b"}
    with pytest.raises(ValueError, match="NVIDIA_API_KEY_2"):
        runner.provider_keys([1, 2], {"NVIDIA_API_KEY": "a"})
    with pytest.raises(ValueError, match="different keys"):
        runner.provider_keys([1, 2], {"NVIDIA_API_KEY": "a", "NVIDIA_API_KEY_2": "a"})


def test_outcome_classes():
    ok = {"task_checksum": "c", "verifier_result": {"rewards": {"reward": 0.0}}}
    assert runner.classify(ok, timed_out=False, expected_checksum="c") == "scored"
    timeout = dict(ok, exception_info={"exception_type": "AgentTimeoutError"})
    assert runner.classify(timeout, timed_out=False, expected_checksum="c") == "scored", "budget exhaustion is a failure"
    assert runner.classify(ok, timed_out=False, expected_checksum="d") == "task_mismatch"
    assert runner.classify(None, timed_out=True, expected_checksum="c") == "outer_timeout"
    build = {"task_checksum": "c", "exception_info": {"exception_type": "RuntimeError"}}
    assert runner.classify(build, timed_out=False, expected_checksum="c") == "infra_error"
    assert runner.classify({"task_checksum": "c"}, timed_out=False, expected_checksum="c") == "verifier_error"
    gap = {"task_checksum": "c", "exception_info": {"exception_type": "RustyCoverageLimitation"}}
    assert runner.classify(gap, timed_out=False, expected_checksum="c") == "coverage_limitation"
    slow = dict(ok, agent_result={"metadata": {"rusty_budget_retry_wait_seconds": 60}},
                agent_execution={"started_at": "2026-10-09T00:00:00", "finished_at": "2026-10-09T00:01:40"})
    assert runner.throttle_confounded(slow)


def test_base_images_skip_digest_pins(tmp_path):
    env = tmp_path / "environment"
    (env / "simcloud").mkdir(parents=True)
    (env / "Dockerfile").write_text("# c\nFROM fullstack-bench/client:dev\nRUN x\n")
    (env / "simcloud" / "Dockerfile").write_text("FROM --platform=linux/amd64 img@sha256:" + "a" * 64 + "\n")
    assert runner.base_images(tmp_path) == ["fullstack-bench/client:dev"]
