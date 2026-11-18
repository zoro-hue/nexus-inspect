"""Runs the scanner over each labelled fixture repository and compares findings to expected.json."""
import json
import os
import tempfile

from nexus.scanner import scan

CORPUS = os.path.join(os.path.dirname(__file__), "fixtures", "corpus")


def load_cases():
    for name in sorted(os.listdir(CORPUS)):
        root = os.path.join(CORPUS, name)
        spec = os.path.join(root, "expected.json")
        if os.path.isfile(spec):
            with open(spec) as fh:
                yield name, root, json.load(fh)


def run_case(root):
    result = scan(root, "2026-01-01T00:00:00Z")
    return {(f["ruleId"], f["filePath"], f["startLine"]) for f in result.findings}, result


def expected_set(spec):
    return {(e["ruleId"], e["filePath"], e["startLine"]) for e in spec["expected"]}
