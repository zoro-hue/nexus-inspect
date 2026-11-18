"""Run the Phase 5 benchmark and write benchmarks/results.json.

Usage: python tools/benchmark.py [--large-files N] [--out PATH]
Scores the labelled synthetic corpus per category, then times a generated large tree (N files, default 1500).
The output labels synthetic and external data separately. Exit status is 0 even when scores are below 1.0;
the scores are reported as measured, not as pass or fail.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexus import benchmark  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--large-files", type=int, default=1500, help="size of the generated large tree; 0 skips it")
    parser.add_argument("--out", default=os.path.join(ROOT, "benchmarks", "results.json"))
    args = parser.parse_args(argv)
    doc = benchmark.report(large_files=args.large_files)
    benchmark.write(doc, args.out)
    overall = doc["corpus"]["overall"]
    print(json.dumps({"out": args.out, "cases": doc["corpus"]["caseCount"], "overall": overall,
                      "large": doc.get("large", {}).get("seconds")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
