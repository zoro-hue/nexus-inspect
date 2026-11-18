"""Deterministic investigation over stored findings.

This layer never invents evidence. Every fact is computed from the stored findings and the analysis coverage, and every
root-cause group lists the finding IDs and files it was built from. Groups are labelled as hypotheses: the rules show
that several findings share a pattern, not that they share a cause. The AI explanation (see ai.explain_group) is an
optional add-on and is validated against the same finding IDs and file:line references.
"""
import hashlib
from collections import defaultdict

LEVEL_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
EVIDENCE_CLASSES = ("confirmed", "potential", "informational")
METHODOLOGY = (
    "Overall risk is the highest finding risk score (severity weight x rule confidence, plus 2 per related finding "
    "in the same file, capped at 100). High: any confirmed critical or high finding, or any score of 60 or more. "
    "Medium: any score of 25 or more. Low: findings exist but none reaches 25. None: no findings. Scores come from "
    "the scanner; this layer does not change them."
)


def component_of(path):
    """Top-level directory of a file. Files at the repository root belong to '(root)'."""
    return path.split("/", 1)[0] if "/" in path else "(root)"


def _group_id(kind, key):
    return "RC-" + hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:10]


def _overall_risk(findings):
    if not findings:
        return "none"
    if any(f["evidenceClass"] == "confirmed" and f["severity"] in ("critical", "high") for f in findings) \
            or any(f["riskScore"] >= 60 for f in findings):
        return "high"
    if any(f["riskScore"] >= 25 for f in findings):
        return "medium"
    return "low"


def _breakdown(findings, key):
    out = {}
    for f in findings:
        out[f[key]] = out.get(f[key], 0) + 1
    return dict(sorted(out.items()))


def _facts(findings, coverage):
    facts = []
    total = len(findings)
    if total:
        by_class = _breakdown(findings, "evidenceClass")
        facts.append({"text": f"{total} findings: " + ", ".join(
            f"{by_class.get(c, 0)} {c}" for c in EVIDENCE_CLASSES) + ".", "source": "findings"})
    files = {f["filePath"] for f in findings}
    facts.append({"text": f"Findings touch {len(files)} files out of {coverage.get('filesScanned', 0)} scanned.",
                  "source": "coverage"})
    for reason, count in coverage.get("skipped", {}).items():
        facts.append({"text": f"{count} file(s) skipped as {reason.replace('_', ' ')}.", "source": "coverage"})
    for path in coverage.get("parseFailures", []):
        facts.append({"text": f"{path} is not valid Python, so AST checks did not run on it; line rules still did.",
                      "source": "coverage"})
    if coverage.get("deduplicated"):
        facts.append({"text": f"{coverage['deduplicated']} overlapping finding(s) were merged into a stronger one on "
                              "the same line; the merged rule IDs are listed in that finding's explanation.",
                      "source": "coverage"})
    if coverage.get("partial"):
        facts.append({"text": f"The analysis is partial: {coverage.get('partialReason') or 'a limit was reached'}.",
                      "source": "coverage"})
    return facts


def _components(findings):
    grouped = defaultdict(list)
    for f in findings:
        grouped[component_of(f["filePath"])].append(f)
    out = []
    for comp, members in grouped.items():
        top = max(members, key=lambda f: f["riskScore"])
        out.append({
            "path": comp,
            "findingCount": len(members),
            "fileCount": len({f["filePath"] for f in members}),
            "highestRisk": top["riskScore"],
            "highestSeverity": top["severity"],
            "categories": sorted({f["category"] for f in members}),
        })
    out.sort(key=lambda c: (-c["highestRisk"], c["path"]))
    return out


def _groups(findings):
    groups = []

    # 1. The same rule fires in several files of one component: a repeated pattern, possibly one shared cause.
    repeated = defaultdict(list)
    for f in findings:
        repeated[(f["ruleId"], component_of(f["filePath"]))].append(f)
    for (rule_id, comp), members in repeated.items():
        files = sorted({m["filePath"] for m in members})
        if len(files) < 2:
            continue
        groups.append({
            "id": _group_id("repeated", f"{rule_id}|{comp}"),
            "kind": "repeated-pattern",
            "label": f"{members[0]['title']} repeats across {len(files)} files in {comp}",
            "hypothesis": "The same pattern in several files often comes from copied code or a missing shared helper. "
                          "Check whether one change can cover all of them.",
            "findingIds": [m["id"] for m in members],
            "files": files,
            "evidenceClass": _weakest(members),
        })

    # 2. Several rules fire in one file: the file may need review as a unit.
    by_file = defaultdict(list)
    for f in findings:
        by_file[f["filePath"]].append(f)
    for path, members in by_file.items():
        rules = {m["ruleId"] for m in members}
        if len(members) < 3 or len(rules) < 2:
            continue
        groups.append({
            "id": _group_id("file", path),
            "kind": "file-concentration",
            "label": f"{len(members)} findings from {len(rules)} rules in {path}",
            "hypothesis": "Several unrelated problems in one file can mean the file was written in one unfinished pass. "
                          "Review it as a unit rather than fixing each line alone.",
            "findingIds": [m["id"] for m in members],
            "files": [path],
            "evidenceClass": _weakest(members),
        })

    # 3. A relative import does not resolve: findings in the importing file may be symptoms of the missing module.
    for f in findings:
        if f["ruleId"] != "GAP-INTEG-001":
            continue
        related = [m for m in by_file[f["filePath"]] if m["id"] != f["id"]]
        groups.append({
            "id": _group_id("import", f"{f['filePath']}|{f['startLine']}"),
            "kind": "broken-import",
            "label": f"{f['filePath']} imports a module that is not in the repository",
            "hypothesis": "The importing file may depend on code that was never added or was moved. Findings in the same "
                          "file could be symptoms of that gap.",
            "findingIds": [f["id"]] + [m["id"] for m in related],
            "files": [f["filePath"]],
            "evidenceClass": "confirmed",
        })

    groups.sort(key=lambda g: (g["kind"], g["id"]))
    return groups


def _weakest(members):
    order = {"informational": 0, "potential": 1, "confirmed": 2}
    return min((m["evidenceClass"] for m in members), key=lambda c: order.get(c, 1))


def build(findings, coverage):
    """Investigation for one analysis. `findings` are the stored summaries; `coverage` is the stored coverage object."""
    ordered = sorted(findings, key=lambda f: (-f["riskScore"], f["filePath"], f["startLine"]))
    return {
        "deterministic": True,
        "overallRisk": _overall_risk(ordered),
        "methodology": METHODOLOGY,
        "counts": {
            "total": len(ordered),
            "byEvidenceClass": _breakdown(ordered, "evidenceClass"),
            "bySeverity": _breakdown(ordered, "severity"),
            "byConfidence": _confidence_bands(ordered),
        },
        "facts": _facts(ordered, coverage or {}),
        "components": _components(ordered),
        "rootCauseGroups": _groups(ordered),
        "prioritized": [f["id"] for f in ordered[:10]],
        "coverage": coverage or {},
        "limitations": [
            "Rules read text and a Python AST; they do not run the code, resolve dynamic imports, or follow data flow across "
            "functions.",
            "Root-cause groups are hypotheses built from shared rules and locations, not proof of a shared cause.",
            "Potential findings need a reviewer's confirmation; informational findings are markers, not defects.",
        ],
    }


def _confidence_bands(findings):
    bands = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        c = f["confidence"]
        bands["high" if c >= 0.8 else "medium" if c >= 0.5 else "low"] += 1
    return bands


def find_group(investigation, group_id):
    for group in investigation["rootCauseGroups"]:
        if group["id"] == group_id:
            return group
    return None
