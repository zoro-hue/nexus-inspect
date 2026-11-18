"""Report generation: JSON, Markdown and SARIF 2.1.0 from one assembled report.

Every export is built from the same persisted analysis, findings, import edges and investigation, so the formats cannot
disagree with each other or with the dashboard. Nothing is added that the stored data does not contain: no AI text, no
unstored findings, and no claims beyond the stored coverage. Repository text is escaped for its output format (Markdown
inline text, Markdown code fences, SARIF URIs), so a file name or a source line cannot change the structure of a report.
"""
import re
from urllib.parse import quote

from . import graph as G
from . import investigation as I
from . import rules as R

SCHEMA_ID = "nexus-inspect.report/v2"
SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/cs01/schemas/sarif-schema-2.1.0.json"
SEVERITY_TO_SARIF = {"critical": "error", "high": "error", "medium": "warning", "low": "note"}
SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = ("security", "slop", "gap", "loose")
CATEGORY_TITLES = {"security": "Security", "slop": "Placeholder and fake implementation", "gap": "Coding gaps",
                   "loose": "Loose ends"}
MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|&#~])")
NO_FINDINGS_NOTE = ("The rules that ran reported no findings. This is not a guarantee that the code is free of defects; "
                    "rules only report the patterns they know.")


def _summary(findings):
    out = {"total": len(findings),
           "bySeverity": {s: 0 for s in SEVERITIES},
           "byCategory": {c: 0 for c in CATEGORIES},
           "byEvidenceClass": {c: 0 for c in I.EVIDENCE_CLASSES},
           "byConfidence": {"high": 0, "medium": 0, "low": 0}}
    for f in findings:
        out["bySeverity"][f["severity"]] += 1
        out["byCategory"][f["category"]] += 1
        out["byEvidenceClass"][f["evidenceClass"]] = out["byEvidenceClass"].get(f["evidenceClass"], 0) + 1
        band = "high" if f["confidence"] >= 0.8 else "medium" if f["confidence"] >= 0.5 else "low"
        out["byConfidence"][band] += 1
    return out


def _location(f):
    end = f["endLine"] if f["endLine"] and f["endLine"] >= f["startLine"] else f["startLine"]
    return f"{f['filePath']}:{f['startLine']}" + (f"-{end}" if end != f["startLine"] else "")


def _executive_summary(analysis, findings, investigation):
    lines = []
    if analysis.get("partial"):
        lines.append(f"This is a partial result: {analysis.get('partialReason') or 'a limit was reached'}. "
                     "Files after the limit were not analysed.")
    lines.append(f"Overall risk is {investigation['overallRisk']}. The method is described in the investigation "
                 "section of this report.")
    if not findings:
        lines.append(NO_FINDINGS_NOTE)
        return lines
    counts = investigation["counts"]["byEvidenceClass"]
    files = len({f["filePath"] for f in findings})
    lines.append(f"{len(findings)} finding{'s' if len(findings) != 1 else ''} across {files} file{'s' if files != 1 else ''}: "
                 f"{counts.get('confirmed', 0)} confirmed, {counts.get('potential', 0)} potential, "
                 f"{counts.get('informational', 0)} informational.")
    serious = sum(1 for f in findings if f["severity"] in ("critical", "high"))
    if serious:
        lines.append(f"{serious} of them {'is' if serious == 1 else 'are'} rated critical or high severity.")
    by_id = {f["id"]: f for f in findings}
    top = [by_id[fid] for fid in investigation["prioritized"][:3] if fid in by_id]
    for f in top:
        lines.append(f"Priority: {f['title']} at {_location(f)} ({f['severity']}, {f['evidenceClass']}).")
    return lines


def _number(value, default, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default, True
    if number != number:  # NaN
        return default, True
    clamped = min(max(number, low), high)
    return clamped, clamped != number


def sanitize(findings):
    """Stored values are trusted for their content but checked for type and range before they reach a report. Anything
    that does not fit is replaced with a safe default and counted, so the report says what it changed."""
    clean, adjusted = [], 0
    for f in findings:
        g = dict(f)
        changed = False
        if g.get("severity") not in SEVERITIES:
            g["severity"], changed = "medium", True
        if g.get("category") not in CATEGORIES:
            g["category"], changed = "gap", True
        if g.get("evidenceClass") not in I.EVIDENCE_CLASSES:
            g["evidenceClass"], changed = "potential", True
        g["confidence"], c1 = _number(g.get("confidence"), 0.5, 0.0, 1.0)
        g["riskScore"], c2 = _number(g.get("riskScore"), 0.0, 0.0, 100.0)
        g["startLine"], c3 = _number(g.get("startLine"), 1, 1, 10 ** 9)
        g["endLine"], c4 = _number(g.get("endLine"), g["startLine"], 1, 10 ** 9)
        g["startLine"], g["endLine"] = int(g["startLine"]), int(max(g["endLine"], g["startLine"]))
        for key in ("title", "explanation", "impact", "remediation", "evidence", "filePath", "ruleId", "id"):
            if not isinstance(g.get(key), str):
                g[key], changed = str(g.get(key) or ""), True
        g.setdefault("verificationSteps", [])
        g.setdefault("relatedFindings", [])
        if changed or c1 or c2 or c3 or c4:
            adjusted += 1
        clean.append(g)
    return clean, adjusted


def build(analysis, project, findings, edges, options=None):
    """The report as a plain dict. `analysis` and `project` are stored rows; `findings` are stored finding dicts;
    `edges` are stored import edges."""
    findings, adjusted = sanitize(findings)
    ordered = sorted(findings, key=lambda f: (-f["riskScore"], f["filePath"], f["startLine"]))
    investigation = I.build(ordered, analysis.get("coverage") or {})
    relations = G.build(ordered, edges, investigation["rootCauseGroups"],
                        {"structure": False, "limit": G.MAX_NODES_LIMIT, "edgeLimit": G.MAX_EDGES_LIMIT})
    limitations = list(investigation["limitations"]) + [
        "No AI-generated text is included in this export. AI explanations are shown only in the dashboard and are "
        "labelled as generated.",
        "Each finding is a rule match at the stated location. Check the evidence before acting on it.",
    ]
    if adjusted:
        limitations.append(f"{adjusted} stored finding(s) had values outside the expected type or range. They were "
                           "shown with safe defaults; the stored record was not changed.")
    if analysis.get("coverage", {}).get("skipped"):
        limitations.append("Some files were skipped: " + ", ".join(
            f"{count} {reason.replace('_', ' ')}" for reason, count in sorted(analysis["coverage"]["skipped"].items())) + ".")
    return {
        "schema": SCHEMA_ID,
        "tool": {"name": "NEXUS INSPECT", "version": _version()},
        "project": {"id": project.get("id"), "name": project.get("name"), "source": project.get("source")},
        "analysis": analysis,
        "summary": _summary(ordered),
        "executiveSummary": _executive_summary(analysis, ordered, investigation),
        "investigation": {k: investigation[k] for k in
                          ("overallRisk", "methodology", "counts", "facts", "components", "rootCauseGroups",
                           "prioritized")},
        "relationships": {"nodes": relations["stats"]["nodes"], "edges": relations["edges"],
                          "truncated": relations["truncated"], "notes": relations["notes"],
                          "importEdges": len(edges)},
        "findings": ordered,
        "limitations": limitations,
    }


def _version():
    from . import __version__
    return __version__


# ----- JSON -----

def to_json(report):
    return report


# ----- Markdown -----

def md_text(value):
    """Escape arbitrary text for a Markdown paragraph or table cell. Newlines are flattened so content stays on one line."""
    text = " ".join(str(value).split())
    return MD_SPECIAL.sub(r"\\\1", text)


def md_span(value, limit=160):
    """Inline code for a short value. The backtick run is longer than any run in the text, so the span cannot end early."""
    text = " ".join(str(value).split())[:limit]
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def md_code(value, limit=400):
    """A code block whose fence is longer than any backtick run in the text, so the text cannot close it early."""
    text = " ".join(str(value).split())[:limit]
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def to_markdown(report):
    a, s = report["analysis"], report["summary"]
    lines = ["# NEXUS INSPECT report", "",
             "## Project and analysis",
             f"- Project: {md_text(report['project'].get('name') or '')}",
             f"- Source: {md_text(report['project'].get('source') or '')}",
             f"- Analysis: `{a['id']}`",
             f"- Status: {md_text(a['status'])}",
             f"- Created: {md_text(a.get('createdAt') or '')}",
             f"- Files scanned: {a.get('filesScanned', 0)}",
             f"- Partial result: {md_text(a['partialReason']) if a.get('partial') else 'no'}",
             f"- Report schema: {SCHEMA_ID}",
             "",
             "## Executive summary"]
    lines += [f"- {md_text(line)}" for line in report["executiveSummary"]]
    lines += ["", "## Overall risk", md_text(report["investigation"]["methodology"]), "",
              f"Overall risk: **{md_text(report['investigation']['overallRisk'])}**.", "",
              "## Severity and confidence",
              "| Severity | Findings |", "| --- | --- |"]
    lines += [f"| {sev} | {n} |" for sev, n in s["bySeverity"].items()]
    lines += ["", "| Confidence band | Findings |", "| --- | --- |"]
    lines += [f"| {band} | {n} |" for band, n in s["byConfidence"].items()]
    lines += ["", "| Evidence class | Findings |", "| --- | --- |"]
    lines += [f"| {cls} | {n} |" for cls, n in s["byEvidenceClass"].items()]
    lines += ["", "## Findings by category"]
    for cat in CATEGORIES:
        lines.append(f"- {CATEGORY_TITLES[cat]}: {s['byCategory'][cat]}")
    lines += ["", "## Prioritized findings", "| # | Finding | Location | Severity | Confidence | Status |",
              "| --- | --- | --- | --- | --- | --- |"]
    by_id = {f["id"]: f for f in report["findings"]}
    for i, fid in enumerate(report["investigation"]["prioritized"], 1):
        f = by_id.get(fid)
        if f:
            lines.append(f"| {i} | {md_text(f['title'])} | {md_text(_location(f))} | {f['severity']} | "
                         f"{f['confidence']:.2f} | {md_text(f['status'])} |")
    if not report["investigation"]["prioritized"]:
        lines.append("| - | No findings were reported. | - | - | - | - |")

    lines += ["", "## Findings"]
    for f in report["findings"]:
        lines += ["", f"### {md_text(f['title'])}", "",
                  f"- ID: `{f['id']}`",
                  f"- Rule: `{f['ruleId']}` ({md_text(f['detectionMethod'])})",
                  f"- Category: {CATEGORY_TITLES.get(f['category'], f['category'])}",
                  f"- Severity: {f['severity']}; confidence {f['confidence']:.2f}; risk score {f['riskScore']}",
                  f"- Classification: {f['evidenceClass']}",
                  f"- Status: {md_text(f['status'])}",
                  f"- Location: {md_text(_location(f))}",
                  "", "Evidence:", "", md_code(f["evidence"]), "",
                  f"Explanation: {md_text(f['explanation'])}",
                  "", f"Why it matters: {md_text(f['impact'])}",
                  "", f"Recommended fix: {md_text(f['remediation'])}",
                  "", "Verification:"]
        lines += [f"{i}. {md_text(step)}" for i, step in enumerate(f["verificationSteps"], 1)]
        if f.get("relatedFindings"):
            related = [by_id[r]["title"] + f" ({_location(by_id[r])})" for r in f["relatedFindings"] if r in by_id]
            if related:
                lines += ["", "Related findings: " + md_text("; ".join(related))]

    lines += ["", "## Cross-file relationships"]
    rel = report["relationships"]
    if not rel["edges"]:
        lines.append("No relationships were found between these findings and files.")
    for e in rel["edges"]:
        lines.append(f"- {md_text(e['explanation'])} *({e['status']})*")
        for ev in e["evidence"][:3]:
            if ev.get("line"):
                lines.append(f"  - {md_text(ev['path'])}:{ev['line']}: {md_span(ev['text'])}")
    if rel["truncated"]:
        lines.append("- Relationships were truncated: " + md_text(" ".join(rel["notes"])))

    lines += ["", "## Root-cause hypotheses"]
    groups = report["investigation"]["rootCauseGroups"]
    if not groups:
        lines.append("No groups were found.")
    for g in groups:
        lines.append(f"- {md_text(g['label'])} ({g['kind']}, {g['evidenceClass']}). "
                     f"Hypothesis: {md_text(g['hypothesis'])}")

    lines += ["", "## Analysis coverage"]
    cov = a.get("coverage") or {}
    lines.append(f"- Files scanned: {cov.get('filesScanned', a.get('filesScanned', 0))}")
    lines.append(f"- Languages: {md_text(', '.join(f'{k} ({v})' for k, v in sorted((a.get('languages') or {}).items())) or 'none')}")
    lines.append(f"- Rules run: {cov.get('ruleCount', 'unknown')}")
    lines.append(f"- Duplicate findings merged: {cov.get('deduplicated', 0)}")
    for reason, count in sorted((cov.get("skipped") or {}).items()):
        lines.append(f"- Skipped as {md_text(reason.replace('_', ' '))}: {count}")
    lines += ["", "## Known limitations"]
    lines += [f"- {md_text(item)}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


# ----- SARIF 2.1.0 -----

def _uri(path):
    """A relative, percent-encoded URI reference using forward slashes, as SARIF artifact locations require."""
    return quote(path.replace("\\", "/"), safe="/")


def to_sarif(report):
    findings = report["findings"]
    analysis = report["analysis"]
    rule_index, rules = {}, []
    for f in findings:
        if f["ruleId"] in rule_index:
            continue
        rule = R.RULES_BY_ID.get(f["ruleId"])
        rule_index[f["ruleId"]] = len(rules)
        rules.append({
            "id": f["ruleId"],
            "name": f["ruleId"].replace("-", ""),
            "shortDescription": {"text": f["title"]},
            "fullDescription": {"text": rule.impact if rule else f["impact"]},
            "help": {"text": (rule.remediation if rule else f["remediation"])},
            "defaultConfiguration": {"level": SEVERITY_TO_SARIF.get(f["severity"], "warning")},
            "properties": {"tags": [f["category"], f["evidenceClass"], f["detectionMethod"]],
                           "category": f["category"], "evidenceClass": f["evidenceClass"]},
        })
    results = []
    for f in findings:
        start = max(1, int(f["startLine"]))
        end = max(start, int(f["endLine"] or start))
        result = {
            "ruleId": f["ruleId"],
            "ruleIndex": rule_index[f["ruleId"]],
            "level": SEVERITY_TO_SARIF.get(f["severity"], "warning"),
            "kind": "fail",
            "message": {"text": f["explanation"]},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": _uri(f["filePath"])},
                "region": {"startLine": start, "endLine": end,
                           "snippet": {"text": f["evidence"][:500]}},
            }}],
            "partialFingerprints": {"nexusFindingId/v1": f["id"]},
            "properties": {"confidence": round(float(f["confidence"]), 2), "riskScore": f["riskScore"],
                           "status": f["status"], "evidenceClass": f["evidenceClass"],
                           "category": f["category"]},
        }
        if f["status"] in ("false_positive", "suppressed"):
            result["suppressions"] = [{"kind": "external", "status": "accepted",
                                       "justification": f"Marked as {f['status']} in NEXUS INSPECT."}]
        results.append(result)
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "NEXUS INSPECT", "version": _version(), "rules": rules}},
            "invocations": [{
                "executionSuccessful": analysis.get("status") == "completed",
                "properties": {"analysisId": analysis.get("id"), "partial": bool(analysis.get("partial")),
                               "partialReason": analysis.get("partialReason")},
            }],
            "results": results,
        }],
    }
