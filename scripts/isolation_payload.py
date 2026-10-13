"""The isolation probe's payload: submitted code that reports what it can reach.

It is deployed through each execution surface exactly as an agent's code would be (a service
command, a build command, a job command, the verifier's sandbox runner) and prints one JSON
line of observations for `scripts/isolation_probe.py` to collect. It only ever tries to read,
list or create; it never changes operator state (a probe file it manages to create is removed).

Observations, each an explicit boolean, or null when there was nothing to probe (no evidence
directory on the image, say), which the gate refuses as unknown rather than reading as a denial:

- workload_uid / operator_uid: this process's uid and the uid of the operator process named by
  SIMCLOUD_PROBE_OPERATOR_PID (the control plane, or the verifier's test runner).
- operator_environment_inherited: an operator secret variable is in this environment.
- private_operator_files_readable: any operator-private file could be read.
- private_operator_directory_writable: a file could be created in an operator-private directory.
- operator_evidence_writable: a file could be created under the evidence directory.
- operator_process_environment_readable: /proc/<operator pid>/environ could be read.

The operator's paths that exist are given in SIMCLOUD_PROBE_CANDIDATES by the driver; the
payload only tries them.

Usage: payload.py <surface> [--serve]  (--serve then answers /healthz until stopped, so the
platform's readiness probe passes and the service instance stays up long enough to be read).
"""

import http.server
import json
import os
import sys
import threading

SECRET_VARS = ("SIMCLOUD_ADMIN_TOKEN", "SIMSAAS_ADMIN_TOKEN", "SIMCLOUD_K8S_AUDIT_TOKEN", "PGPASSWORD")
PRIVATE_FILES = ("/var/lib/simcloud/state.db", "/var/lib/simcloud/pg.admin", "/var/lib/simcloud/state.oidc.pem",
                 "/var/lib/simcloud/state.kms", "/var/lib/simcloud/state.urlkey", "/seed/seed.yaml",
                 "/tests/expected.json", "/tests/test_outputs.py", "/tests/views.json", "/logs/verifier/reward.json")
PRIVATE_DIRS = ("/var/lib/simcloud", "/var/lib/simcloud/logs", "/var/lib/simcloud/artifacts", "/seed", "/tests",
                "/logs/verifier")
EVIDENCE_DIRS = ("/evidence", "/logs/verifier")


def readable(path):
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def writable(directory):
    probe = os.path.join(directory, ".isolation-probe")
    try:
        with open(probe, "w") as f:
            f.write("probe\n")
    except OSError:
        return False
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def any_or_unknown(flags):
    """True if any candidate was reachable, False if every candidate refused, None (unknown) when
    there was no candidate at all: a probe that found nothing to try has observed nothing."""
    flags = list(flags)
    return any(flags) if flags else None


def candidates():
    """Which operator paths exist. The driver, as the operator, records them in
    SIMCLOUD_PROBE_CANDIDATES (JSON: files, dirs, evidence); the workload cannot stat a path it
    may not enter, so judging existence itself would read the boundary working as nothing to
    observe. Without the variable (a standalone run) the payload falls back to its own view."""
    raw = os.environ.get("SIMCLOUD_PROBE_CANDIDATES")
    if raw:
        given = json.loads(raw)
        return given.get("files", []), given.get("dirs", []), given.get("evidence", []), "operator"
    return ([p for p in PRIVATE_FILES if os.path.exists(p)], [d for d in PRIVATE_DIRS if os.path.isdir(d)],
            [d for d in EVIDENCE_DIRS if os.path.isdir(d)], "workload")


def observe(surface):
    operator_pid = os.environ.get("SIMCLOUD_PROBE_OPERATOR_PID") or "1"
    files, dirs, evidence, judged_by = candidates()
    try:
        operator_uid = os.stat(f"/proc/{operator_pid}").st_uid
    except OSError:
        operator_uid = -1
    return {
        "surface": surface, "pid": os.getpid(), "cwd": os.getcwd(),
        "workload_uid": os.getuid(), "workload_euid": os.geteuid(), "operator_uid": operator_uid,
        "operator_pid": int(operator_pid) if str(operator_pid).isdigit() else None,
        "operator_environment_inherited": any(v in os.environ for v in SECRET_VARS),
        "private_operator_files_readable": any_or_unknown(readable(p) for p in files),
        "private_operator_directory_writable": any_or_unknown(writable(d) for d in dirs),
        "operator_evidence_writable": any_or_unknown(writable(d) for d in evidence),
        "operator_process_environment_readable": readable(f"/proc/{operator_pid}/environ"),
        "candidates": {"files": files, "dirs": dirs, "evidence": evidence, "existence_judged_by": judged_by},
        "readable_private_files": [p for p in files if readable(p)],
        "writable_private_dirs": [d for d in dirs if writable(d)],
        "writable_evidence_dirs": [d for d in evidence if writable(d)],
        "env_names": sorted(os.environ),
    }


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *args):
        pass


def main():
    surface = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    print("ISOLATION_PROBE " + json.dumps(observe(surface)), flush=True)
    if "--serve" in sys.argv:
        port = int(os.environ.get("PORT", "8080"))
        server = http.server.HTTPServer(("0.0.0.0", port), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
