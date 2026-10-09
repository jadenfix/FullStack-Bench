"""Fail-closed checks of operator-collected workload and open-loop traffic samples.

These predicates do not collect evidence or establish isolation. Use them only
with operator-owned receipts and independent expected input/output manifests.
Runtime growth is a measured budget, not an asymptotic-complexity proof.
"""

import math
import re
from statistics import median


def _number(value, *, zero=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value >= 0 if zero else value > 0))


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def assess_performance(
    contract: dict, candidate: list[dict], baseline: list[dict], *,
    expected: dict[tuple[int, str, int], dict], task_sha256: str,
    candidate_sha256: str, baseline_sha256: str, machine_id: str,
) -> dict:
    """Compare complete paired samples to a correct, slower independent baseline.

    expected maps (size, distribution, repeat) to input_sha256/output_sha256.
    Hashes must be produced by an independent semantic output canonicalizer,
    preserving multiplicities and only disregarding order if the brief allows it.
    The starting buggy program is not a valid correctness baseline.
    """
    def invalid(reason):
        return {"ok": False, "status": "invalid_evidence", "reason": reason}
    try:
        sizes, repetitions = contract["sizes"], contract["repetitions"]
        distributions = contract["distributions"]
        if (len(sizes) < 3 or sizes != sorted(set(sizes))
                or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in sizes)
                or not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 3
                or len(distributions) < 2 or len(set(distributions)) != len(distributions)
                or not all(isinstance(d, str) and d for d in distributions)):
            return invalid("incomplete workload contract")
        for key in ("min_speedup", "max_growth_ratio", "max_peak_rss_mb"):
            if not _number(contract[key]):
                return invalid("nonfinite or nonpositive performance budget")
        if len({sizes[i + 1] / sizes[i] for i in range(len(sizes) - 1)}) != 1:
            return invalid("workload sizes lack a single published growth factor")
        keys = {(n, d, r) for n in sizes for d in distributions for r in range(repetitions)}
        if set(expected) != keys or not machine_id or not all(_sha(s) for s in (task_sha256, candidate_sha256, baseline_sha256)):
            return invalid("expected manifests or execution pins are incomplete")
        for item in expected.values():
            if not all(_sha(item[name]) for name in ("input_sha256", "output_sha256")):
                return invalid("independent workload manifests are malformed")
        tracks = []
        for label, rows, artifact in (("candidate", candidate, candidate_sha256), ("baseline", baseline, baseline_sha256)):
            by_key = {}
            for row in rows:
                if (not isinstance(row["size"], int) or isinstance(row["size"], bool)
                        or not isinstance(row["repeat"], int) or isinstance(row["repeat"], bool)):
                    return invalid(f"{label} contains a malformed sample identity")
                key = row["size"], row["distribution"], row["repeat"]
                if key in by_key or key not in keys:
                    return invalid(f"{label} contains duplicate or unexpected samples")
                if (row["task_sha256"] != task_sha256 or row["artifact_sha256"] != artifact
                        or row["machine_id"] != machine_id or row["input_sha256"] != expected[key]["input_sha256"]):
                    return invalid(f"{label} sample differs from execution or input pins")
                if not _number(row["elapsed_ms"]) or not _number(row["peak_rss_mb"]):
                    return invalid(f"{label} sample lacks valid operator timing or memory")
                if not isinstance(row["exit_code"], int) or isinstance(row["exit_code"], bool):
                    return invalid(f"{label} sample has a malformed exit status")
                if row["exit_code"] != 0:
                    return {"ok": False, "status": "workload_failure", "reason": f"{label} workload did not finish"}
                if row["output_sha256"] != expected[key]["output_sha256"]:
                    return {"ok": False, "status": "incorrect_output", "reason": f"{label} output differs from independent expected results"}
                by_key[key] = row
            if set(by_key) != keys:
                return invalid(f"{label} dropped required workload samples")
            tracks.append(by_key)
        results = {}
        for distribution in distributions:
            medians = {
                n: median(tracks[0][n, distribution, r]["elapsed_ms"] for r in range(repetitions))
                for n in sizes
            }
            growth = [medians[b] / medians[a] for a, b in zip(sizes, sizes[1:])]
            paired_speedup = median(
                tracks[1][sizes[-1], distribution, r]["elapsed_ms"]
                / tracks[0][sizes[-1], distribution, r]["elapsed_ms"]
                for r in range(repetitions)
            )
            peak = max(tracks[0][n, distribution, r]["peak_rss_mb"] for n in sizes for r in range(repetitions))
            # Preserve spread rather than silently discard slow outliers.
            spread = {
                n: max(tracks[0][n, distribution, r]["elapsed_ms"] for r in range(repetitions))
                / min(tracks[0][n, distribution, r]["elapsed_ms"] for r in range(repetitions))
                for n in sizes
            }
            results[distribution] = {
                "speedup": paired_speedup, "growth": growth, "peak_rss_mb": peak,
                "median_ms": medians, "max_min_ratio": spread,
                "ok": (paired_speedup >= contract["min_speedup"]
                       and all(g <= contract["max_growth_ratio"] for g in growth)
                       and peak <= contract["max_peak_rss_mb"]),
            }
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, ZeroDivisionError):
        return invalid("malformed workload receipt")
    ok = all(result["ok"] for result in results.values())
    return {"ok": ok, "status": "pass" if ok else "performance_failure", "distributions": results}


def assess_live_traffic(rows: list[dict], *, arrivals_ns: dict[str, int], p99_ms: float, error_budget: float) -> dict:
    """Arrival-to-completion latency includes queues, retries and backoff.

    arrivals_ns is the operator's precommitted open-loop arrival manifest. There
    must be a completion (including timeouts) for every scheduled request. No
    percentile from success-only samples and no closed-loop replacement traffic.
    """
    def invalid(reason):
        return {"ok": False, "status": "invalid_evidence", "reason": reason}
    if (not arrivals_ns or not _number(p99_ms) or not _number(error_budget, zero=True)
            or error_budget >= 1 or any(not isinstance(t, int) or isinstance(t, bool) or t < 0 for t in arrivals_ns.values())):
        return invalid("invalid arrival manifest or SLO")
    samples, failures = {}, 0
    try:
        for row in rows:
            request = row["request_id"]
            if request in samples or request not in arrivals_ns:
                return invalid("duplicate or unscheduled customer request")
            start, end = row["arrival_ns"], row["completed_ns"]
            if (not isinstance(start, int) or isinstance(start, bool) or start != arrivals_ns[request]
                    or not isinstance(end, int) or isinstance(end, bool) or end < start):
                return invalid("invalid logical request timestamps")
            if type(row["application_ok"]) is not bool:
                return invalid("missing independent application outcome")
            failures += not row["application_ok"]
            samples[request] = (end - start) / 1_000_000
        if set(samples) != set(arrivals_ns):
            return invalid("scheduled arrivals lack completion receipts")
    except (KeyError, TypeError, ValueError, OverflowError):
        return invalid("malformed customer request receipt")
    latencies = sorted(samples.values())
    p99 = latencies[math.ceil(0.99 * len(latencies)) - 1]
    rate = failures / len(latencies)
    ok = p99 <= p99_ms and rate <= error_budget
    return {"ok": ok, "status": "pass" if ok else "slo_failure", "p99_ms": p99,
            "error_rate": rate, "requests": len(latencies), "errors": failures}
