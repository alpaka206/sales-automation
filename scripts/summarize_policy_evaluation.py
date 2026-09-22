"""Aggregate explicitly reviewed live outputs; never call a model or production DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    reviews = read(args.reviews)
    public_fixture = read(Path(__file__).resolve().parents[1] / "tests/fixtures/policy_response_live.json")
    fixture_hash = hashlib.sha256(json.dumps(public_fixture, ensure_ascii=False, sort_keys=True,
                                            default=str).encode()).hexdigest()
    policy_hash = hashlib.sha256(json.dumps(public_fixture["policies"], ensure_ascii=False, sort_keys=True,
                                           default=str).encode()).hexdigest()
    root = Path(args.raw_root)
    summary = {"review_method": reviews["method"], "runs": {}, "development_only": True,
               "review_sha256": hashlib.sha256(Path(args.reviews).read_bytes()).hexdigest()}
    public_answers = []
    for variant in ("baseline", "candidate"):
        results = read(root / variant / "results.json")
        calls = read(root / variant / "calls.json")
        manifest = read(root / variant / "manifest.json")
        labels = {r["run"]: r for r in reviews[variant]}
        if len(labels) != len(results) or set(labels) != {r["run"] for r in results}:
            raise ValueError("Every run must have exactly one review")
        if (manifest["data_origin"] != "SYNTHETIC_DEVELOPMENT_CASES_NOT_COMPANY_POLICY"
                or manifest["dataset_sha256"] != fixture_hash or manifest["policy_sha256"] != policy_hash):
            raise ValueError("Publishing answers is restricted to synthetic fixtures")
        metrics = {}
        for arm in manifest["arms"]:
            rows = [r for r in results if r["arm"] == arm]
            timings = [r["seconds"] for r in rows]
            ids = {r["run"] for r in rows}
            usage = [c for c in calls if c["run"] in ids]
            for r in rows:
                if r["status"] != "GENERATED" and labels[r["run"]]["verdict"] == "PASS":
                    raise ValueError("A blocked draft cannot count as a valid answer")
            metrics[arm] = {
                "attempted": len(rows), "generated": sum(r["status"] == "GENERATED" for r in rows),
                "valid_answers": sum(labels[r["run"]]["verdict"] == "PASS" for r in rows),
                "failed_answers": sum(labels[r["run"]]["verdict"] == "FAIL" for r in rows),
                "review_required": sum(labels[r["run"]]["verdict"] == "REVIEW_REQUIRED" for r in rows),
                "critical_error_outputs": sum(labels[r["run"]]["critical"] for r in rows),
                "generation_repaired": sum(r.get("provenance", {}).get("limited_evidence_checks", {}).get(
                    "generation_attempts", 1) > 1 for r in rows),
                "actual_provider_calls": len(usage),
                "input_tokens": sum(c.get("input_tokens", 0) for c in usage),
                "candidate_output_tokens": sum(c.get("candidate_output_tokens", 0) for c in usage),
                "thinking_tokens": None, "billed_cost": None,
                "p50_seconds": round(statistics.median(timings), 3),
                "p95_seconds_nearest_rank": percentile(timings, .95),
            }
        summary["runs"][variant] = {"manifest": manifest, "metrics": metrics,
                                     "results_sha256": hashlib.sha256((root / variant / "results.json").read_bytes()).hexdigest()}
        for r in results:
            public_answers.append({"variant": variant, **{k: r.get(k) for k in (
                "run", "status", "body", "error_type", "seconds")}, "review": labels[r["run"]]})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_name("reviewed-synthetic-answers.json").write_text(
        json.dumps(public_answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({v: r["metrics"] for v, r in summary["runs"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
