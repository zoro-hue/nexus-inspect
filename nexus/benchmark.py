"""Benchmark: scan labelled fixture repositories and score each finding against the expected labels.

Two kinds of data are measured, and the report labels them separately:
  - "synthetic-labelled-corpus": fixtures written by the project, each with hand-written ground truth in expected.json.
    Scores show agreement with those labels only. They are not evidence of real-world accuracy.
  - "synthetic-generated-large": a repository generated at run time to measure time and resource use on a larger tree,
    with one planted issue. It measures speed and noise, not detection quality across real code.
External repositories are not part of this report unless they are listed in an external section with their own labels.
"""
import json
import os
import platform
import tempfile
import time

from . import ai
from . import rules as R
from .scanner import scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "tests", "fixtures", "corpus")
CATEGORIES = ("security", "slop", "gap", "loose")
TIMESTAMP = "2026-01-01T00:00:00Z"


def load_cases(corpus=CORPUS):
    for name in sorted(os.listdir(corpus)):
        root = os.path.join(corpus, name)
        spec = os.path.join(root, "expected.json")
        if os.path.isfile(spec):
            with open(spec, encoding="utf-8") as fh:
                yield name, root, json.load(fh)


def _key(entry):
    return (entry["ruleId"], entry["filePath"], entry["startLine"])


def score(predicted, expected):
    """Set-based precision, recall and F1. A metric is None when its denominator is zero."""
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (
        0.0 if precision is not None and recall is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _round(precision), "recall": _round(recall), "f1": _round(f1)}


def _round(value):
    return None if value is None else round(value, 4)


def _category(rule_id):
    rule = R.RULES_BY_ID.get(rule_id)
    return rule.category if rule else "unknown"


def _by_category(predicted, expected):
    out = {}
    for cat in CATEGORIES:
        p = {k for k in predicted if _category(k[0]) == cat}
        e = {k for k in expected if _category(k[0]) == cat}
        if p or e:
            out[cat] = score(p, e)
    return out


def run_case(root):
    started = time.perf_counter()
    result = scan(root, TIMESTAMP)
    elapsed = time.perf_counter() - started
    predicted = {(f["ruleId"], f["filePath"], f["startLine"]) for f in result.findings}
    return predicted, result, elapsed


def run_corpus(corpus=CORPUS):
    cases, predicted_all, expected_all = [], set(), set()
    for name, root, spec in load_cases(corpus):
        expected = {_key(e) for e in spec["expected"]}
        predicted, result, elapsed = run_case(root)
        cases.append({
            "case": name,
            "description": spec.get("description", ""),
            "expected": len(expected),
            "predicted": len(predicted),
            "exactMatch": predicted == expected,
            "seconds": round(elapsed, 4),
            "filesScanned": result.files_scanned,
            "skipped": dict(sorted(result.files_skipped.items())),
            "unmatchedPredicted": sorted(map(list, predicted - expected)),
            "missed": sorted(map(list, expected - predicted)),
        })
        # Scores are pooled over every case, so a single case cannot dominate the averages.
        predicted_all |= {(name, *k) for k in predicted}
        expected_all |= {(name, *k) for k in expected}
    return {
        "dataset": "synthetic-labelled-corpus",
        "dataLabel": "Synthetic fixtures with hand-written labels. Agreement with these labels is not real-world accuracy.",
        "caseCount": len(cases),
        "overall": score(predicted_all, expected_all),
        "perCategory": _by_category({k[1:] for k in predicted_all}, {k[1:] for k in expected_all}),
        "cases": cases,
    }


def generate_large(directory, files=1500, packages=10):
    """A generated tree of small modules that all import each other through package __init__ files, so the only
    expected finding is the one planted stub. Returns the expected label list."""
    expected = []
    per_pkg = files // packages
    planted_at = files // 2
    for p in range(packages):
        pkg_dir = os.path.join(directory, f"pkg{p:02d}")
        os.makedirs(pkg_dir, exist_ok=True)
        init_lines = []
        for m in range(per_pkg):
            index = p * per_pkg + m
            name = f"mod{index:05d}"
            init_lines.append(f"from . import {name}")
            body = f"def value_{index}(x):\n    return x + {index}\n"
            if index == planted_at:
                body = "def planted_stub(x):\n    pass\n"
                expected.append(("LOOSE-STUB-001", f"pkg{p:02d}/{name}.py", 1))
            with open(os.path.join(pkg_dir, f"{name}.py"), "w", encoding="utf-8") as fh:
                fh.write(body)
        with open(os.path.join(pkg_dir, "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(init_lines) + "\n")
    return expected


def run_large(files=1500):
    with tempfile.TemporaryDirectory(prefix="nexus-bench-") as tmp:
        expected = {(rule, path, line) for rule, path, line in generate_large(tmp, files=files)}
        predicted, result, elapsed = run_case(tmp)
    return {
        "dataset": "synthetic-generated-large",
        "dataLabel": "Generated tree with one planted stub. Measures time and noise, not detection quality on real code.",
        "files": files,
        "filesScanned": result.files_scanned,
        "findings": len(predicted),
        "seconds": round(elapsed, 4),
        "partial": bool(result.partial),
        "score": score(predicted, expected),
    }


def report(corpus=CORPUS, large_files=1500):
    doc = {
        "generatedWith": "nexus.benchmark",
        "environment": {"python": platform.python_version(), "platform": platform.system()},
        "aiProvider": {"enabled": ai.status()["enabled"],
                       "note": "Findings are deterministic. The benchmark never calls an AI provider."},
        "externalValidation": {"status": "not run",
                               "note": "No external repositories were analysed for this report."},
        "corpus": run_corpus(corpus),
    }
    if large_files:
        doc["large"] = run_large(large_files)
    return doc


def write(doc, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
