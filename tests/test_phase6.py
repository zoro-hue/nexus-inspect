"""Phase 6 regression tests: audit fixes, secret redaction, graph evidence, report exports and SARIF structure."""
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from unittest import mock

from nexus import api, auth, graph as G, investigation as I, report as REPORT, rules as R, scanner, service
from nexus import store as S
from nexus.store import Store

import server  # the HTTP entry point at the project root

from tests import helpers
from tests.sarif_check import validate as sarif_errors


def mem(test):
    """An in-memory store that is closed when the test ends."""
    store = Store(":memory:")
    test.addCleanup(store.close)
    return store


def finding(fid, path, line, *, rule="GAP-ERR-001", title="Finding", severity="medium", category="gap",
            evidence_class="potential", risk=20.0, status="open", end=None, evidence="x = 1"):
    return {"id": fid, "ruleId": rule, "title": title, "category": category, "severity": severity,
            "confidence": 0.8, "status": status, "evidenceClass": evidence_class, "filePath": path,
            "startLine": line, "endLine": end or line, "evidence": evidence, "explanation": f"{title} explained.",
            "impact": "Impact text.", "remediation": "Fix it.", "verificationSteps": ["Check it."],
            "relatedFindings": [], "detectionMethod": "pattern", "riskScore": risk,
            "createdAt": "2026-11-17T00:00:00Z", "excerpt": []}


def edge(src, dst, line, statement="import x"):
    return {"source": src, "target": dst, "line": line, "statement": statement, "kind": "python-import"}


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def project_and_analysis(store, status="completed", owner=None):
    pid = store.create_project("demo", "upload:demo", owner)
    aid = store.create_analysis(pid)
    store.update_analysis(aid, status=status, stage=status)
    return pid, aid


class StoreAuditTests(unittest.TestCase):
    def test_failed_batch_leaves_no_partial_findings(self):
        """Reproduced defect: a failed save_findings left earlier rows pending, and a later unrelated commit kept them."""
        store = mem(self)
        _, aid = project_and_analysis(store, status="running")
        good = finding("NX-1", "a.py", 1)
        bad = dict(finding("NX-2", "a.py", 2))
        del bad["title"]
        with self.assertRaises(KeyError):
            store.save_findings(aid, [good, bad])
        store.update_analysis(aid, status="completed")
        self.assertEqual(store.list_findings(aid), [])

    def test_failed_migration_leaves_schema_and_version_unchanged(self):
        """Reproduced defect: a failing migration left a half-applied column and the old version number."""
        path = os.path.join(tempfile.mkdtemp(), "m.sqlite")
        bad = dict(S.MIGRATIONS)
        bad[S.SCHEMA_VERSION + 1] = "ALTER TABLE projects ADD COLUMN extra TEXT; SELECT * FROM missing_table;"
        with mock.patch.dict(S.MIGRATIONS, bad, clear=True):
            with self.assertLogs("nexus.store", level="ERROR"), self.assertRaises(sqlite3.Error):
                Store(path)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], S.SCHEMA_VERSION)
        self.assertFalse(any(row[1] == "extra" for row in con.execute("PRAGMA table_info(projects)")))
        con.close()
        Store(path).close()  # the real migrations still apply cleanly afterwards

    def test_analyses_left_running_are_marked_failed_on_restart(self):
        """Reproduced defect: an analysis interrupted by a stop stayed 'running' forever and held a concurrency slot."""
        path = os.path.join(tempfile.mkdtemp(), "r.sqlite")
        store = Store(path)
        owner = store.create_user("r@example.com", "R", "x")
        _, aid = project_and_analysis(store, status="running", owner=owner)
        store.close()
        with self.assertLogs("nexus.store", level="WARNING"):
            reopened = Store(path)
        analysis = reopened.get_analysis(aid)
        self.assertEqual(analysis["status"], "failed")
        self.assertEqual(analysis["errorCode"], "interrupted")
        self.assertEqual(reopened.count_active_analyses(owner), 0)
        reopened.close()

    def test_expired_sessions_are_removed_when_a_new_session_starts(self):
        store = mem(self)
        uid = store.create_user("s@example.com", "S", "x")
        store.create_session("old-digest", uid, "2000-01-01T00:00:00Z")
        store.create_session("new-digest", uid, auth.expiry_iso())
        rows = store._db.execute("SELECT token_hash FROM sessions").fetchall()
        self.assertEqual([r[0] for r in rows], ["new-digest"])

    def test_sign_in_throttle_table_is_bounded(self):
        with mock.patch.object(auth, "MAX_TRACKED_EMAILS", 50):
            auth._failures.clear()
            for i in range(200):
                auth.record_failure(f"user{i}@example.com")
            self.assertLessEqual(len(auth._failures), 50)
            auth._failures.clear()


class SecretExposureTests(unittest.TestCase):
    AWS = "AKIAABCDEFGHIJKLMNOP"
    GH = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

    def test_provider_tokens_are_redacted_wherever_they_appear(self):
        for raw in (self.AWS, self.GH, "sk-ant-api03-" + "a" * 40):
            line = f'config_value = "{raw}"'
            self.assertNotIn(raw, R.redact_line(line))

    def test_identifiers_that_only_look_similar_are_not_redacted(self):
        line = 'pipeline = "sk-learn-pipeline-utilities"'
        self.assertEqual(R.redact_line(line), line)

    def test_token_rule_reports_the_line_without_the_value(self):
        hits = R.RULES_BY_ID["SEC-SECRET-004"].check("src/cfg.py", [f'token = "{self.GH}"'])
        self.assertEqual([h.line for h in hits], [1])
        self.assertNotIn(self.GH, hits[0].evidence)

    def test_private_key_is_reported_and_its_body_never_reaches_an_excerpt(self):
        body = "\n".join(["MIIEowIBAAKCAQEAabc%03d" % i for i in range(5)])
        text = f"import os\n-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\nprint(1)\n"
        files = {"keys/server.pem": text}
        hits = R.RULES_BY_ID["SEC-SECRET-003"].check("keys/server.pem", text.splitlines())
        self.assertEqual([h.line for h in hits], [2])
        excerpt = scanner._excerpt(text.splitlines(), 2, 2)
        shown = " ".join(row["text"] for row in excerpt)
        self.assertNotIn("MIIEowIBAAKCAQEA", shown)
        self.assertIn(R.PEM_REDACTED, shown)
        self.assertIsNotNone(files)

    def test_scan_result_contains_no_raw_token_in_findings_or_excerpts(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "settings.py"), "w") as fh:
            fh.write(f'import os\nkey = "{self.AWS}"\nother = 1\nthird = 2\n')
        result = scanner.scan(root, "2026-11-17T00:00:00Z")
        blob = json.dumps(result.findings)
        self.assertNotIn(self.AWS, blob)
        self.assertTrue(any(f["ruleId"] == "SEC-SECRET-004" for f in result.findings))

    def test_ai_prompt_built_from_a_finding_contains_no_raw_secret(self):
        from nexus import ai
        f = finding("NX-9", "cfg.py", 2, rule="SEC-SECRET-004")
        f["excerpt"] = [{"line": 2, "text": R.redact_line(f'k = "{self.AWS}"'), "highlight": True}]
        self.assertNotIn(self.AWS, ai.build_prompt(f))


class ApiHardeningTests(unittest.TestCase):
    def setUp(self):
        self.store = mem(self)

    def test_non_string_branch_gets_a_json_error_not_a_crash(self):
        status, _, body = helpers.handle(self.store, "POST", "/api/analyses/url",
                                         json.dumps({"url": "https://github.com/a/b", "branch": 123}).encode(),
                                         {"Content-Type": "application/json"})
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_branch")

    def test_unexpected_errors_return_generic_500_without_internal_detail(self):
        with mock.patch.object(service, "start_url", side_effect=RuntimeError("secret internal detail /srv/x")), \
                self.assertLogs("nexus.api", level="ERROR"):
            status, _, body = helpers.handle(self.store, "POST", "/api/analyses/url",
                                             json.dumps({"url": "https://github.com/a/b"}).encode(),
                                             {"Content-Type": "application/json"})
        self.assertEqual(status, 500)
        text = body.decode()
        self.assertIn("internal_error", text)
        self.assertNotIn("secret internal detail", text)
        self.assertNotIn("/srv/x", text)

    def test_global_cap_on_active_analyses_returns_503(self):
        owner = helpers.user_id(self.store)
        project_and_analysis(self.store, status="queued", owner=owner)
        with mock.patch.dict(os.environ, {"NEXUS_MAX_ACTIVE_ANALYSES": "1"}):
            status, _, body = helpers.handle(self.store, "POST", "/api/analyses/url",
                                             json.dumps({"url": "https://github.com/a/b"}).encode(),
                                             {"Content-Type": "application/json"})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "server_busy")

    def test_global_cap_on_live_streams(self):
        with mock.patch.object(server, "EVENT_STREAMS_TOTAL", 2), mock.patch.object(server, "EVENT_STREAMS_PER_USER", 9):
            server._open_streams.clear()
            server._open_total = 0
            self.assertTrue(server._acquire_stream("u1"))
            self.assertTrue(server._acquire_stream("u2"))
            self.assertFalse(server._acquire_stream("u3"))
            server._release_stream("u1")
            self.assertTrue(server._acquire_stream("u3"))
            server._open_streams.clear()
            server._open_total = 0

    def test_per_user_stream_cap_does_not_block_other_accounts(self):
        with mock.patch.object(server, "EVENT_STREAMS_TOTAL", 64), mock.patch.object(server, "EVENT_STREAMS_PER_USER", 2):
            server._open_streams.clear()
            server._open_total = 0
            self.assertTrue(server._acquire_stream("alice"))
            self.assertTrue(server._acquire_stream("alice"))
            self.assertFalse(server._acquire_stream("alice"))   # alice is at her own limit
            self.assertTrue(server._acquire_stream("bob"))      # bob is unaffected
            server._release_stream("alice")
            self.assertTrue(server._acquire_stream("alice"))    # alice may open again after one closes
            server._open_streams.clear()
            server._open_total = 0

    def test_responses_carry_security_headers_over_real_http(self):
        httpd, _ = server.build_server("127.0.0.1", 0, ":memory:")
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            for path in ("/", "/health"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
                self.assertEqual(headers["x-frame-options"], "DENY")
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertIn("camera=()", headers["permissions-policy"])
                self.assertNotIn("unsafe-eval", headers["content-security-policy"])
        finally:
            server.stop_server(httpd)

    def test_markdown_export_escapes_repository_text(self):
        f = finding("NX-7", "web/a[b](c).js", 3, title="Bad `title` [link](http://evil.example)",
                    evidence="x = `y` | pipe ## heading")
        analysis = {"id": "ana_1", "status": "completed", "createdAt": "2026-11-17T00:00:00Z", "filesScanned": 1,
                    "partial": False, "partialReason": None, "coverage": {}, "languages": {}, "projectId": "prj"}
        project = {"id": "prj", "name": "demo", "source": "upload:demo"}
        md = REPORT.to_markdown(REPORT.build(analysis, project, [f], []))
        self.assertNotIn("[link](http://evil.example)", md)
        self.assertNotIn("\n## heading", md)
        self.assertIn("\\[link\\]", md)
        self.assertIn("```\nx = `y` | pipe ## heading\n```", md)  # fence longer than the backtick run in the text


class GraphExtractionTests(unittest.TestCase):
    def test_python_relative_and_src_layout_imports_resolve_with_lines(self):
        files = {
            "src/app/__init__.py": "",
            "src/app/models.py": "class User: pass\n",
            "src/app/services/billing.py": "from ..models import User\nfrom app.models import User as U2\n",
        }
        edges = G.extract_import_edges(files)
        targets = {(e["source"], e["target"], e["line"]) for e in edges}
        self.assertIn(("src/app/services/billing.py", "src/app/models.py", 1), targets)
        self.assertIn(("src/app/services/billing.py", "src/app/models.py", 2), targets)

    def test_strings_comments_and_third_party_modules_create_no_edges(self):
        files = {
            "pkg/a.py": '# from . import b\nimport requests\nmsg = "from . import b"\n',
            "pkg/b.py": "x = 1\n",
        }
        self.assertEqual(G.extract_import_edges(files), [])

    def test_javascript_relative_imports_resolve_extensions_and_index_files(self):
        files = {
            "web/search.js": "import helper from './util';\nconst x = require('./util/index.js');\n"
                             "import lazy from './missing';\nimport css from './style.css';\n",
            "web/util.js": "export default 1;\n",
            "web/util/index.js": "export const y = 2;\n",
        }
        edges = G.extract_import_edges(files)
        pairs = {(e["target"], e["line"]) for e in edges}
        self.assertEqual(pairs, {("web/util.js", 1), ("web/util/index.js", 2)})

    def test_every_extracted_edge_names_a_real_repository_file(self):
        files = {"a/x.py": "from . import y\n", "a/y.py": "", "b/s.js": "import './../a/x.py';\n"}
        for e in G.extract_import_edges(files):
            self.assertIn(e["source"], files)
            self.assertIn(e["target"], files)
            self.assertTrue(e["statement"])


class GraphBuildTests(unittest.TestCase):
    def setUp(self):
        self.findings = [
            finding("NX-1", "web/search.js", 4, rule="GAP-INTEG-001", title="Broken import", severity="high",
                    category="gap", evidence_class="confirmed", risk=40),
            finding("NX-2", "src/util.py", 9, rule="SLOP-STUB-001", title="Placeholder", severity="medium",
                    category="slop", evidence_class="potential", risk=25),
            finding("NX-3", "src/util.py", 20, rule="LOOSE-TODO-001", title="Marker", severity="low",
                    category="loose", evidence_class="informational", risk=6),
        ]
        self.edges = [edge("web/search.js", "src/util.py", 2, "from util import x"),
                      edge("src/app.py", "src/util.py", 5, "import util")]
        self.groups = I.build(self.findings, {})["rootCauseGroups"]

    def test_every_edge_has_evidence_an_explanation_and_a_known_status(self):
        g = G.build(self.findings, self.edges, self.groups, {"structure": True})
        self.assertTrue(g["edges"])
        for e in g["edges"]:
            self.assertTrue(e["explanation"], e)
            self.assertTrue(e["evidence"], e)
            self.assertIn(e["status"], G.STATUS_ORDER)

    def test_impact_paths_are_potential_and_cite_the_import(self):
        g = G.build(self.findings, self.edges, self.groups, {"structure": False})
        impact = [e for e in g["edges"] if e["type"] == "impact"]
        self.assertTrue(impact)
        self.assertTrue(all(e["status"] == "potential" for e in impact))
        self.assertTrue(all("hypothesis" in e["explanation"] for e in impact))
        for e in impact:
            self.assertEqual(e["evidence"][0]["path"], e["target"][len("file:"):])

    def test_informational_findings_do_not_create_impact_paths(self):
        g = G.build(self.findings, self.edges, self.groups, {"structure": False})
        self.assertFalse(any(e["type"] == "impact" and e["source"] == "fnd:NX-3" for e in g["edges"]))

    def test_filters_narrow_findings_and_their_edges(self):
        g = G.build(self.findings, self.edges, self.groups, {"category": "loose", "structure": False})
        ids = {n["id"] for n in g["nodes"] if n["kind"] == "finding"}
        self.assertEqual(ids, {"fnd:NX-3"})
        self.assertFalse(any(e["type"] == "member_of" for e in g["edges"]))

    def test_focus_keeps_only_the_node_and_its_direct_connections(self):
        g = G.build(self.findings, self.edges, self.groups, {"focus": "file:src/util.py", "structure": False})
        keep = {n["id"] for n in g["nodes"]}
        self.assertIn("file:src/util.py", keep)
        self.assertIn("fnd:NX-2", keep)
        for e in g["edges"]:
            self.assertTrue(e["source"] in keep and e["target"] in keep)

    def test_unknown_focus_returns_a_clear_empty_state(self):
        g = G.build(self.findings, self.edges, self.groups, {"focus": "file:nope.py"})
        self.assertTrue(g["empty"])
        self.assertTrue(g["notes"])

    def test_empty_repository_has_an_empty_graph_with_a_note(self):
        g = G.build([], [], [], {})
        self.assertTrue(g["empty"])
        self.assertEqual(g["edges"], [])

    def test_large_graph_is_limited_and_says_so(self):
        many = [finding(f"NX-{i}", f"pkg/m{i}.py", i + 1, risk=float(i)) for i in range(400)]
        g = G.build(many, [], [], {"limit": 50, "structure": False})
        self.assertTrue(g["truncated"])
        self.assertLessEqual(len([n for n in g["nodes"] if n["kind"] == "finding"]), 50)
        self.assertTrue(any("Showing" in note for note in g["notes"]))
        top = [n for n in g["nodes"] if n["kind"] == "finding"][0]
        self.assertEqual(top["findingId"], "NX-399")  # highest risk is kept first

    def test_group_membership_evidence_cites_each_members_own_line(self):
        g = G.build(self.findings, self.edges, self.groups, {"structure": False})
        members = [e for e in g["edges"] if e["type"] == "member_of"]
        for e in members:
            self.assertTrue(e["evidence"][0]["line"] > 0)


class GraphEndpointTests(unittest.TestCase):
    def setUp(self):
        self.store = mem(self)

    def test_graph_endpoint_validates_parameters(self):
        _, aid = project_and_analysis(self.store, owner=helpers.user_id(self.store))
        for query in ("category=bogus", "severity=extreme", "evidenceClass=maybe", "minRisk=500", "limit=abc",
                      "limit=99999", "structure=yes", "focus=%20bad%20"):
            status, _, _ = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/graph?{query}")
            self.assertEqual(status, 400, query)

    def test_graph_endpoint_returns_an_empty_state_for_an_analysis_without_findings(self):
        _, aid = project_and_analysis(self.store, owner=helpers.user_id(self.store))
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/graph")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["empty"])

    def test_graph_is_private_to_the_owner(self):
        other = self.store.create_user("other@example.com", "O", "x")
        _, aid = project_and_analysis(self.store, owner=other)
        status, _, _ = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/graph")
        self.assertEqual(status, 404)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.store = mem(self)
        self.owner = helpers.user_id(self.store)
        self.project = {"id": "prj_x", "name": "demo", "source": "upload:demo"}

    def analysis(self, **over):
        base = {"id": "ana_r", "status": "completed", "createdAt": "2026-11-17T00:00:00Z", "filesScanned": 3,
                "partial": False, "partialReason": None, "coverage": {"ruleCount": 18, "skipped": {},
                                                                    "deduplicated": 0},
                "languages": {"Python": 3}, "projectId": "prj_x"}
        base.update(over)
        return base

    def test_json_report_has_sections_and_keeps_the_original_keys(self):
        findings = [finding("NX-1", "a.py", 3, severity="high", evidence_class="confirmed", risk=40)]
        report = REPORT.build(self.analysis(), self.project, findings, [edge("b.py", "a.py", 1)])
        for key in ("schema", "analysis", "summary", "executiveSummary", "investigation", "relationships",
                    "findings", "limitations", "project"):
            self.assertIn(key, report)
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["summary"]["byEvidenceClass"]["confirmed"], 1)

    def test_markdown_report_has_every_required_section(self):
        findings = [finding("NX-1", "a.py", 3, title="Thing")]
        md = REPORT.to_markdown(REPORT.build(self.analysis(), self.project, findings, []))
        for heading in ("# NEXUS INSPECT report", "## Executive summary", "## Overall risk",
                        "## Severity and confidence", "## Findings by category", "## Prioritized findings",
                        "## Findings", "## Cross-file relationships", "## Root-cause hypotheses",
                        "## Analysis coverage", "## Known limitations"):
            self.assertIn(heading, md)

    def test_sarif_for_a_real_report_is_structurally_valid(self):
        findings = [finding("NX-1", "web/my file.js", 3, rule="SEC-SECRET-004", severity="critical",
                            category="security", evidence_class="confirmed", risk=50),
                    finding("NX-2", "a.py", 0, status="false_positive", end=0)]
        sarif = REPORT.to_sarif(REPORT.build(self.analysis(), self.project, findings, []))
        self.assertEqual(sarif_errors(sarif), [])
        results = sarif["runs"][0]["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"], "web/my%20file.js")
        self.assertTrue(any("suppressions" in r for r in results))
        self.assertTrue(all(r["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1 for r in results))

    def test_empty_report_is_valid_sarif_and_says_no_defects_are_guaranteed(self):
        report = REPORT.build(self.analysis(), self.project, [], [])
        self.assertEqual(sarif_errors(REPORT.to_sarif(report)), [])
        self.assertIn(REPORT.NO_FINDINGS_NOTE, report["executiveSummary"])
        self.assertEqual(REPORT.to_sarif(report)["runs"][0]["results"], [])

    def test_failed_analysis_report_marks_execution_unsuccessful(self):
        report = REPORT.build(self.analysis(status="failed", partial=True, partialReason="time budget"),
                              self.project, [], [])
        sarif = REPORT.to_sarif(report)
        self.assertFalse(sarif["runs"][0]["invocations"][0]["executionSuccessful"])
        self.assertTrue(any("partial" in line.lower() for line in report["executiveSummary"]))
        self.assertIn("Status: failed", REPORT.to_markdown(report))

    def test_large_report_stays_valid_and_bounded(self):
        findings = [finding(f"NX-{i}", f"pkg/f{i % 40}.py", (i % 90) + 1, risk=float(i % 60)) for i in range(800)]
        started = time.monotonic()
        report = REPORT.build(self.analysis(), self.project, findings, [])
        sarif = REPORT.to_sarif(report)
        md = REPORT.to_markdown(report)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(len(sarif["runs"][0]["results"]), 800)
        self.assertEqual(sarif_errors(sarif), [])
        self.assertIn("## Findings", md)

    def test_malformed_values_are_clamped_and_disclosed_not_exported_as_invalid(self):
        bad = finding("NX-1", "a.py", 0, end=-3)
        bad["confidence"] = "not a number"
        bad["severity"] = "urgent"
        report = REPORT.build(self.analysis(), self.project, [bad], [])
        sarif = REPORT.to_sarif(report)
        self.assertEqual(sarif_errors(sarif), [])
        region = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        self.assertEqual(region["startLine"], 1)
        self.assertGreaterEqual(region["endLine"], region["startLine"])
        self.assertTrue(any("safe defaults" in item for item in report["limitations"]))
        self.assertEqual(report["findings"][0]["severity"], "medium")

    def test_non_finite_and_out_of_range_numbers_are_replaced(self):
        findings = [finding("NX-2", "a.py", 3, risk=float("nan")), finding("NX-3", "b.py", 4)]
        findings[1]["confidence"] = 7.5
        report = REPORT.build(self.analysis(), self.project, findings, [])
        values = {f["id"]: f for f in report["findings"]}
        self.assertEqual(values["NX-2"]["riskScore"], 0.0)
        self.assertEqual(values["NX-3"]["confidence"], 1.0)

    def test_report_endpoint_rejects_unknown_formats_and_names_downloads(self):
        _, aid = project_and_analysis(self.store, owner=self.owner)
        status, _, _ = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/report?format=pdf")
        self.assertEqual(status, 400)
        status, headers, _ = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/report?format=sarif")
        self.assertEqual(status, 200)
        self.assertIn("sarif+json", headers["Content-Type"])
        self.assertIn(f'filename="nexus-report-{aid}.sarif"', headers["Content-Disposition"])

    def test_end_to_end_exports_from_a_real_scanned_repository(self):
        archive = make_zip({
            "demo/src/app/models.py": "class User:\n    pass\n",
            "demo/src/app/views.py": "from .models import User\n\ndef show():\n    return 'a password = \"hunter2hunter2\"'\n",
            "demo/web/search.js": "import helper from './missing';\n",
        })
        analysis = service.run_archive_analysis(self.store, "demo", "upload:demo", archive, owner_id=self.owner)
        self.assertEqual(analysis["status"], "completed")
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{analysis['id']}/report?format=json")
        report = json.loads(body)
        self.assertEqual(report["summary"]["total"], len(report["findings"]))
        self.assertTrue(any(e["type"] == "imports" for e in report["relationships"]["edges"]))
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{analysis['id']}/report?format=sarif")
        self.assertEqual(sarif_errors(json.loads(body)), [])
        self.assertNotIn("hunter2hunter2", body.decode())


if __name__ == "__main__":
    unittest.main()
