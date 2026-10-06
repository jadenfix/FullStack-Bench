"""Run operator probes against an isolated, already-deployed delivery candidate.

Uses real HTTP/MCP and PostgreSQL. DATABASE_URL is loaded from the environment, never argv
or output. The target must be dedicated to this rehearsal: probes create orders, temporarily
reject one order's journal insert, and restart services via a supplied argv array.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fsbench.mutation_scenarios import CASES, Scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "orders-url",
        "concierge-url",
        "customer",
        "address",
        "restart-command-json",
        "output",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--isolated-probe-target", action="store_true", required=True)
    args = parser.parse_args()
    command = json.loads(args.restart_command_json)
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) for x in command)
    ):
        parser.error(
            "restart-command-json must be a nonempty JSON array of argv strings"
        )

    def restart():
        subprocess.run(
            command,
            check=True,
            timeout=130,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    scenarios = Scenarios(
        args.orders_url,
        os.environ["DATABASE_URL"],
        args.customer,
        args.address,
        restart,
        args.concierge_url,
    )
    results = scenarios.run()
    output = {
        "protocol": "delivery-mutations/v1",
        "cases": results,
        "passed": sum(x["ok"] is True for x in results.values()),
        "total": len(CASES),
    }
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(f"{output['passed']}/{output['total']} checks passed; receipt saved")
    return 0 if output["passed"] == output["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
