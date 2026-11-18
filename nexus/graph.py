"""Repository relationships and the investigation graph.

Two layers, both computed from data the scanner has already read:

1. `extract_import_edges` records each import that resolves to a file in the scanned repository, with the file, the
   line and the (redacted) statement. Python uses the `ast` module, so string literals and comments cannot create an
   edge. JavaScript and TypeScript use a line-based pattern, so multi-line import statements are not recorded. Imports
   of standard-library or third-party modules never resolve to a repository file and produce no edge.

2. `build` turns stored findings, stored import edges and root-cause groups into a graph. Every edge carries the
   evidence that supports it (a finding location, an import line, or a grouping rule), and it is labelled as confirmed,
   potential, informational or structural. A relationship without evidence is not created.
"""
import ast
import os
import re

from . import rules as R

JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
JS_SPEC_RE = re.compile(
    r"""(?:\bimport\s+(?:[^'"`;]*?\s+from\s+)?|\bexport\s+[^'"`;]*?\s+from\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)"""
    r"""['"](\.{1,2}/[^'"]+)['"]""")
STATEMENT_MAX = 160
MAX_EVIDENCE = 5
MAX_NODES_DEFAULT = 120
MAX_NODES_LIMIT = 300
MAX_EDGES_LIMIT = 600
IMPACT_DEPENDENTS_PER_FINDING = 5
IMPACT_SEVERITIES = ("critical", "high", "medium")
STATUS_ORDER = {"confirmed": 0, "potential": 1, "informational": 2, "structure": 3}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CATEGORY_ORDER = ("security", "slop", "gap", "loose")


def _statement(text):
    return R.redact_line(text.strip())[:STATEMENT_MAX]


def _python_candidates(base_dirs, module_parts, files):
    """Yield repository files a dotted module could refer to, nearest directory first."""
    for folder in base_dirs:
        stem = "/".join(p for p in [folder, *module_parts] if p)
        for candidate in (stem + ".py", stem + "/__init__.py"):
            if candidate in files:
                yield candidate
                break


def _ancestors(path):
    """Directories that can hold a top-level package for `path`, nearest first, ending at the repository root."""
    parts = path.split("/")[:-1]
    out = []
    for i in range(len(parts), 0, -1):
        out.append("/".join(parts[:i]))
    out.append("")
    return out


def _resolve_js(path, spec, files):
    target = os.path.normpath(os.path.join(os.path.dirname(path), spec)).replace(os.sep, "/")
    options = [target] if os.path.splitext(target)[1] in JS_EXTS else []
    options += [target + e for e in JS_EXTS] + [target + "/index" + e for e in JS_EXTS]
    for option in options:
        if option in files:
            return option
    return None


def _python_edges(path, text, files):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []  # unparseable files produce no edges; the coverage record already names them
    lines = text.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                for target in _python_candidates(_ancestors(path), parts, files):
                    out.append((target, node.lineno))
                    break
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                folder = os.path.dirname(path)
                for _ in range(node.level - 1):
                    folder = os.path.dirname(folder)
                base = [folder]
            else:
                base = _ancestors(path)
            module = node.module.split(".") if node.module else []
            hit = next(_python_candidates(base, module, files), None) if module or node.level else None
            if hit:
                out.append((hit, node.lineno))
            # `from pkg import submodule` also depends on pkg/submodule.py when that file exists.
            for alias in node.names:
                sub = next(_python_candidates(base, module + [alias.name], files), None)
                if sub and sub != hit:
                    out.append((sub, node.lineno))
    edges = []
    for target, line in out:
        if target == path:
            continue
        statement = lines[line - 1] if 0 < line <= len(lines) else ""
        edges.append({"source": path, "target": target, "line": line, "statement": _statement(statement),
                      "kind": "python-import"})
    return edges


def _js_edges(path, text, files):
    out = []
    for idx, line in enumerate(text.splitlines(), 1):
        for m in JS_SPEC_RE.finditer(line):
            target = _resolve_js(path, m.group(1), files)
            if target and target != path:
                out.append({"source": path, "target": target, "line": idx, "statement": _statement(line),
                            "kind": "js-import"})
    return out


def extract_import_edges(texts):
    """Resolved import edges for a scanned repository: {relative path: text}. Deterministic and de-duplicated."""
    files = set(texts)
    edges = []
    for path in sorted(texts):
        extension = R.ext(path)
        if extension == ".py":
            edges += _python_edges(path, texts[path], files)
        elif extension in JS_EXTS:
            edges += _js_edges(path, texts[path], files)
    seen, unique = set(), []
    for e in edges:
        key = (e["source"], e["target"], e["line"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


# ----- graph -----

def _component(path):
    return path.split("/", 1)[0] if "/" in path else "(root)"


def _ev(path, line, text):
    return {"path": path, "line": line, "text": text}


def build(findings, edges, groups, options=None):
    """Graph for one analysis.

    `findings`: stored findings (already in investigation order). `edges`: stored import edges. `groups`: root-cause
    groups from investigation.build. `options`: focus (node id), category, severity, evidenceClass, minRisk,
    structure (bool), limit (int), edgeLimit (int).
    Returns nodes, edges, stats and a note for every rule that limited what is shown.
    """
    options = options or {}
    category = options.get("category")
    severity = options.get("severity")
    evidence_class = options.get("evidenceClass")
    min_risk = options.get("minRisk") or 0
    structure = bool(options.get("structure", True))
    limit = min(max(int(options.get("limit") or MAX_NODES_DEFAULT), 10), MAX_NODES_LIMIT)
    edge_limit = min(max(int(options.get("edgeLimit") or MAX_EDGES_LIMIT), 10), MAX_EDGES_LIMIT)
    focus = options.get("focus")

    selected = [f for f in findings
                if (not category or f["category"] == category)
                and (not severity or f["severity"] == severity)
                and (not evidence_class or f["evidenceClass"] == evidence_class)
                and f["riskScore"] >= min_risk]
    selected_ids = {f["id"] for f in selected}
    selected_by_id = {f["id"]: f for f in selected}
    by_file = {}
    for f in selected:
        by_file.setdefault(f["filePath"], []).append(f)

    nodes, node_ids = {}, set()
    all_edges = []

    def add_node(node):
        if node["id"] not in nodes:
            nodes[node["id"]] = node
            node_ids.add(node["id"])

    # Finding and file nodes, with an edge from each finding to its file.
    for f in selected:
        add_node({"id": f"fnd:{f['id']}", "kind": "finding", "label": f["title"], "findingId": f["id"],
                  "severity": f["severity"], "category": f["category"], "evidenceClass": f["evidenceClass"],
                  "filePath": f["filePath"], "startLine": f["startLine"], "risk": f["riskScore"]})
    for path, members in by_file.items():
        add_node({"id": f"file:{path}", "kind": "file", "label": path, "path": path,
                  "component": _component(path), "findingCount": len(members),
                  "risk": max(m["riskScore"] for m in members)})
        for m in members:
            ev = m["evidence"]
            all_edges.append({
                "type": "affects", "source": f"fnd:{m['id']}", "target": f"file:{path}",
                "status": m["evidenceClass"],
                "label": "affects this file",
                "explanation": f"{m['title']} is reported at {path}:{m['startLine']}"
                               + (f"-{m['endLine']}" if m["endLine"] != m["startLine"] else "") + ".",
                "evidence": [_ev(path, m["startLine"], ev)],
            })

    # Import relationships between files that take part in the view. Dependency neighbours are added as context.
    import_targets = {}
    for e in edges:
        if e["source"] in by_file or e["target"] in by_file:
            import_targets.setdefault((e["source"], e["target"]), []).append(e)
    for (src, dst), items in sorted(import_targets.items()):
        for node_path in (src, dst):
            if f"file:{node_path}" not in node_ids:
                add_node({"id": f"file:{node_path}", "kind": "file", "label": node_path, "path": node_path,
                          "component": _component(node_path), "findingCount": len(by_file.get(node_path, [])),
                          "risk": max([m["riskScore"] for m in by_file.get(node_path, [])] or [0]),
                          "context": True})
        items = sorted(items, key=lambda e: e["line"])
        all_edges.append({
            "type": "imports", "source": f"file:{src}", "target": f"file:{dst}", "status": "confirmed",
            "label": f"imports ({len(items)})",
            "explanation": f"{src} imports {dst} at line{'s' if len(items) > 1 else ''} "
                           + ", ".join(str(e["line"]) for e in items[:MAX_EVIDENCE]) + ".",
            "evidence": [_ev(src, e["line"], e["statement"]) for e in items[:MAX_EVIDENCE]],
        })
        # Potential impact: a finding in the imported file can reach the importing file.
        for m in sorted(by_file.get(dst, []), key=lambda f: -f["riskScore"]):
            if m["severity"] not in IMPACT_SEVERITIES or m["evidenceClass"] == "informational":
                continue
            if sum(1 for x in all_edges if x["type"] == "impact" and x["source"] == f"fnd:{m['id']}") \
                    >= IMPACT_DEPENDENTS_PER_FINDING:
                break
            add_node({"id": f"file:{src}", "kind": "file", "label": src, "path": src,
                      "component": _component(src), "findingCount": len(by_file.get(src, [])),
                      "risk": max([x["riskScore"] for x in by_file.get(src, [])] or [0]), "context": True})
            all_edges.append({
                "type": "impact", "source": f"fnd:{m['id']}", "target": f"file:{src}", "status": "potential",
                "label": "could affect",
                "explanation": f"{src} imports {dst} (line {items[0]['line']}), where this finding is. A change to "
                               f"{dst} can affect {src}. This is a reachability hypothesis, not a confirmed defect in {src}.",
                "evidence": [_ev(src, items[0]["line"], items[0]["statement"]),
                             _ev(dst, m["startLine"], m["evidence"])],
            })

    # Root-cause groups that contain selected findings become group nodes.
    for g in groups:
        members = [fid for fid in g["findingIds"] if fid in selected_ids]
        if len(members) < 2:
            continue
        add_node({"id": f"grp:{g['id']}", "kind": "group", "label": g["label"], "groupId": g["id"],
                  "groupKind": g["kind"], "evidenceClass": g["evidenceClass"]})
        for fid in members:
            m = selected_by_id[fid]
            all_edges.append({
                "type": "member_of", "source": f"fnd:{fid}", "target": f"grp:{g['id']}",
                "status": g["evidenceClass"], "label": "shares a pattern",
                "explanation": f"{m['filePath']}:{m['startLine']} is in this group: {g['label']}. The {g['kind']} rule "
                               "groups it as a hypothesis about a shared cause.",
                "evidence": [_ev(m["filePath"], m["startLine"], m["evidence"])],
            })

    # Structure: component nodes contain their files, and component dependencies are summarised with their evidence.
    if structure:
        for path in list(by_file) + [n["path"] for n in nodes.values() if n["kind"] == "file" and n.get("context")]:
            comp = _component(path)
            add_node({"id": f"cmp:{comp}", "kind": "component", "label": comp, "path": comp})
        for node in [n for n in nodes.values() if n["kind"] == "file"]:
            all_edges.append({
                "type": "contains", "source": f"cmp:{node['component']}", "target": node["id"], "status": "structure",
                "label": "contains", "explanation": f"{node['path']} is inside the {node['component']} directory.",
                "evidence": [_ev(node["path"], 0, node["path"])],
            })
        cross = {}
        for e in edges:
            a, b = _component(e["source"]), _component(e["target"])
            if a != b and (f"file:{e['source']}" in node_ids and f"file:{e['target']}" in node_ids):
                cross.setdefault((a, b), []).append(e)
        for (a, b), items in sorted(cross.items()):
            all_edges.append({
                "type": "component_imports", "source": f"cmp:{a}", "target": f"cmp:{b}", "status": "confirmed",
                "label": f"{len(items)} import{'s' if len(items) != 1 else ''}",
                "explanation": f"{a} imports {b} in {len(items)} place{'s' if len(items) != 1 else ''}.",
                "evidence": [_ev(e["source"], e["line"], e["statement"]) for e in items[:MAX_EVIDENCE]],
            })

    # Focus: keep the chosen node and everything directly connected to it.
    if focus:
        if focus not in nodes:
            return _empty(f"No node named {focus} is in this view.", options)
        keep = {focus}
        for e in all_edges:
            if e["source"] == focus or e["target"] == focus:
                keep.update([e["source"], e["target"]])
        all_edges = [e for e in all_edges if e["source"] in keep and e["target"] in keep]
        nodes = {k: v for k, v in nodes.items() if k in keep}

    # Ranking and limits. Nodes are kept by priority; edges are kept only when both ends are kept.
    notes = []
    total_nodes, total_edges = len(nodes), len(all_edges)
    def priority(n):
        base = {"finding": 0, "group": 1, "file": 2, "component": 3}[n["kind"]]
        return (base, -n.get("risk", 0), n.get("label", ""))
    if focus:
        kept_nodes = set(nodes)
    else:
        kept_nodes = {n["id"] for n in sorted(nodes.values(), key=priority)[:limit]}
    if len(kept_nodes) < total_nodes:
        notes.append(f"Showing the {len(kept_nodes)} highest-priority of {total_nodes} nodes. Use filters or select "
                     "a node to see the rest of its relationships.")
    edges_kept = [e for e in all_edges if e["source"] in kept_nodes and e["target"] in kept_nodes
                  and (structure or e["type"] != "contains")]
    edges_kept.sort(key=lambda e: (STATUS_ORDER.get(e["status"], 9), e["type"], e["source"], e["target"]))
    if len(edges_kept) > edge_limit:
        notes.append(f"Showing {edge_limit} of {len(edges_kept)} relationships, strongest evidence first.")
        edges_kept = edges_kept[:edge_limit]
    for i, e in enumerate(edges_kept):
        e["id"] = f"edg-{i + 1}"
    out_nodes = [nodes[k] for k in sorted(kept_nodes, key=lambda k: priority(nodes[k]))]
    if not selected and not focus:
        notes.append("No findings match these filters. Imports and structure are still shown when the repository has them.")
    return {
        "nodes": out_nodes,
        "edges": edges_kept,
        "stats": {"nodes": len(out_nodes), "edges": len(edges_kept), "totalNodes": total_nodes,
                  "totalEdges": total_edges, "findings": len(selected), "importEdges": len(edges)},
        "truncated": len(out_nodes) < total_nodes or len(edges_kept) < total_edges,
        "notes": notes,
        "empty": not out_nodes,
        "focus": focus or None,
        "legend": {
            "statuses": {"confirmed": "Confirmed by a rule or a statically resolved import",
                         "potential": "Heuristic or reachability hypothesis; needs review",
                         "informational": "Marker only, not a defect",
                         "structure": "Directory structure from file paths"},
        },
    }


def _empty(note, options):
    return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0, "totalNodes": 0, "totalEdges": 0,
                                                "findings": 0, "importEdges": 0},
            "truncated": False, "notes": [note], "empty": True, "focus": options.get("focus"),
            "legend": {"statuses": {}}}
