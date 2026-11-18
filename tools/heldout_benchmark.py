"""Score the held-out labelled corpus (tests/fixtures/heldout) and print per-case and overall results.

Held-out cases were written to test detection of categories that were not used to tune the rules.
Usage: python tools/heldout_benchmark.py [--out PATH]
Results are measured, not thresholds: the exit status is 0 whatever the scores.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexus import benchmark  # noqa: E402

HELDOUT = os.path.join(ROOT, "tests", "fixtures", "heldout")
HELDOUT2 = os.path.join(ROOT, "tests", "fixtures", "heldout2")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=None, help="optional path to write the JSON report")
    parser.add_argument("--set", default="heldout", choices=["heldout", "heldout2"],
                        help="heldout: rule-tuning set 1 (not independent); heldout2: independent set 2")
    args = parser.parse_args(argv)
    root = HELDOUT if args.set == "heldout" else HELDOUT2
    doc = benchmark.run_corpus(root)
    # Scored scope: each case is scored only for the rule ids it labels. Hits from other rules are
    # reported as unlabelled, so a missing label never inflates or hides a score.
    doc["scoredScope"] = "labelled-rule-ids-per-case"
    for c in doc["cases"]:
        labelled = {e["ruleId"] for e in json.load(open(os.path.join(root, c["case"], "expected.json")))["expected"]}
        c["labelledRuleIds"] = sorted(labelled)
        c["unlabelledHits"] = [k for k in c["unmatchedPredicted"] if k[0] not in labelled]
        c["unmatchedPredicted"] = [k for k in c["unmatchedPredicted"] if k[0] in labelled]
    scored_pred = set()
    scored_exp = set()
    for name in os.listdir(root):
        spec = os.path.join(root, name, "expected.json")
        if not os.path.isfile(spec):
            continue
        labelled = {e["ruleId"] for e in json.load(open(spec))["expected"]}
        pred, _, _ = benchmark.run_case(os.path.join(root, name))
        exp = {benchmark._key(e) for e in json.load(open(spec))["expected"]}
        scored_pred |= {(name, *k) for k in pred if k[0] in labelled}
        scored_exp |= {(name, *k) for k in exp}
    doc["scopedOverall"] = benchmark.score(scored_pred, scored_exp)
    doc["dataset"] = "synthetic-heldout-corpus-" + args.set
    doc["dataLabel"] = ("Held-out synthetic fixtures written separately from rule tuning. "
                        "Agreement with these labels is not real-world accuracy.")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    print(json.dumps({"cases": doc["caseCount"], "scopedOverall": doc["scopedOverall"], "overall_all_rules": doc["overall"]}, indent=2))
    for c in doc["cases"]:
        print(f"{'OK  ' if c['exactMatch'] else 'DIFF'} {c['case']:<16} expected={c['expected']} predicted={c['predicted']} "
              f"missed={c['missed']} unmatched={c['unmatchedPredicted']} unlabelled={c['unlabelledHits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
