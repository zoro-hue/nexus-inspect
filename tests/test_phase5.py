"""Phase 5 tests: detection engine, investigation layer, group AI validation, API ownership and the benchmark."""
import io
import json
import os
import tempfile
import unittest
import zipfile

from nexus import ai, api, auth, benchmark, investigation
from nexus import rules as R
from nexus import service
from nexus.scanner import scan
from nexus.store import Store
from tests import helpers

CORPUS = benchmark.CORPUS


def fixture(name):
    return os.path.join(CORPUS, name)


def findings_of(name):
    return scan(fixture(name), "2026-01-01T00:00:00Z").findings


def ids_of(findings):
    return {(f["ruleId"], f["filePath"], f["startLine"]) for f in findings}


class DetectionEngineTests(unittest.TestCase):
    def test_every_rule_has_an_evidence_class(self):
        for rule in R.RULES:
            self.assertIn(rule.evidence_class, ("confirmed", "potential", "informational"), rule.id)

    def test_findings_carry_the_evidence_class_of_their_rule(self):
        for f in findings_of("python_placeholders"):
            self.assertEqual(f["evidenceClass"], R.RULES_BY_ID[f["ruleId"]].evidence_class)

    def test_todo_marker_is_informational(self):
        todo = [f for f in findings_of("multi_language") if f["ruleId"] == "LOOSE-TODO-001"]
        self.assertEqual([f["evidenceClass"] for f in todo], ["informational"])

    def test_broken_relative_import_is_found_across_files_and_resolved_imports_are_not(self):
        broken = ids_of(findings_of("multi_file_correlation"))
        self.assertIn(("GAP-INTEG-001", "pkg/app.py", 1), broken)
        self.assertFalse([f for f in findings_of("multi_file_correlation")
                          if f["ruleId"] == "GAP-INTEG-001" and f["filePath"] == "web/index.js"])

    def test_asset_imports_are_not_reported_as_broken(self):
        files = {"src/app.js": "import './styles.css';\nimport logo from './logo.png';\n"}
        self.assertEqual(R.check_broken_local_import(files), [])

    def test_js_import_resolves_index_files_and_extensions(self):
        files = {
            "src/app.js": "import a from './lib';\nimport b from './util.ts';\nconst c = require('./data.json');\n",
            "src/lib/index.js": "export default 1;\n",
            "src/util.ts": "export const u = 1;\n",
            "src/data.json": "{}\n",
        }
        self.assertEqual(R.check_broken_local_import(files), [])

    def test_python_relative_import_of_missing_module_is_reported_with_its_line(self):
        files = {"pkg/a.py": "x = 1\nfrom .gone import thing\n"}
        hits = R.check_broken_local_import(files)
        self.assertEqual([(p, h.line) for p, h in hits], [("pkg/a.py", 2)])

    def test_generated_and_minified_files_are_skipped_with_reasons(self):
        result = scan(fixture("generated_and_minified"), "2026-01-01T00:00:00Z")
        self.assertEqual(result.findings, [])
        self.assertEqual(result.files_skipped.get("generated"), 1)
        self.assertEqual(result.files_skipped.get("minified"), 1)

    def test_binary_files_are_skipped(self):
        result = scan(fixture("binary_files"), "2026-01-01T00:00:00Z")
        self.assertEqual(result.files_skipped.get("binary"), 1)
        self.assertEqual(result.findings, [])

    def test_malformed_python_is_recorded_and_line_rules_still_run(self):
        result = scan(fixture("malformed_python"), "2026-01-01T00:00:00Z")
        self.assertEqual(result.parse_failures, ["src/broken.py"])
        self.assertEqual(result.files_skipped.get("unparseable_python"), 1)
        self.assertIn(("SEC-SECRET-001", "src/broken.py", 2), ids_of(result.findings))

    def test_secret_value_never_appears_in_any_finding(self):
        for f in findings_of("malformed_python"):
            self.assertNotIn("k7Qp2mZx9Lr4Wt8Vb", json.dumps(f))

    def test_overlapping_pattern_findings_on_one_line_merge_and_keep_the_other_rule_id(self):
        strong = {"id": "a", "ruleId": "SEC-SQL-001", "category": "security", "confidence": 0.85,
                  "filePath": "x.py", "startLine": 3, "explanation": "SQL.", "detectionMethod": "pattern"}
        weak = {"id": "b", "ruleId": "SEC-EVAL-001", "category": "security", "confidence": 0.6,
                "filePath": "x.py", "startLine": 3, "explanation": "Eval.", "detectionMethod": "pattern"}
        from nexus.scanner import _deduplicate
        kept, dropped = _deduplicate([weak, strong])
        self.assertEqual(dropped, 1)
        self.assertEqual([f["ruleId"] for f in kept], ["SEC-SQL-001"])
        self.assertIn("Also matched by SEC-EVAL-001", kept[0]["explanation"])

    def test_file_level_findings_on_a_code_line_are_never_merged(self):
        stub = {"id": "a", "ruleId": "LOOSE-STUB-001", "category": "loose", "confidence": 0.9,
                "filePath": "x.py", "startLine": 1, "explanation": "Stub.", "detectionMethod": "pattern"}
        mod = {"id": "b", "ruleId": "LOOSE-MOD-001", "category": "loose", "confidence": 0.45,
               "filePath": "x.py", "startLine": 1, "explanation": "Module.", "detectionMethod": "import-graph"}
        from nexus.scanner import _deduplicate
        kept, dropped = _deduplicate([stub, mod])
        self.assertEqual(dropped, 0)
        self.assertEqual(sorted(f["ruleId"] for f in kept), ["LOOSE-MOD-001", "LOOSE-STUB-001"])

    def test_coverage_lists_what_was_scanned_and_skipped(self):
        result = scan(fixture("generated_and_minified"), "2026-01-01T00:00:00Z")
        cov = service.coverage_of(result)
        self.assertEqual(cov["skipped"], {"generated": 1, "minified": 1})
        self.assertEqual(cov["ruleCount"], len(R.RULES))
        self.assertFalse(cov["partial"])


class InvestigationTests(unittest.TestCase):
    def test_repeated_pattern_group_lists_the_real_findings_and_files(self):
        findings = findings_of("multi_file_correlation")
        inv = investigation.build(findings, {"filesScanned": 5})
        groups = [g for g in inv["rootCauseGroups"] if g["kind"] == "repeated-pattern"]
        stub = [g for g in groups if g["label"].startswith("Function body is a placeholder")]
        self.assertEqual(len(stub), 1)
        self.assertEqual(stub[0]["files"], ["pkg/exports.py", "pkg/reports.py"])
        self.assertEqual(len(stub[0]["findingIds"]), 2)

    def test_broken_import_group_is_labelled_as_a_hypothesis(self):
        inv = investigation.build(findings_of("multi_file_correlation"), {})
        group = next(g for g in inv["rootCauseGroups"] if g["kind"] == "broken-import")
        self.assertIn("may depend", group["hypothesis"])
        self.assertEqual(group["files"], ["pkg/app.py"])

    def test_every_group_references_only_findings_that_exist(self):
        findings = findings_of("python_placeholders") + findings_of("multi_file_correlation")
        known = {f["id"] for f in findings}
        for g in investigation.build(findings, {})["rootCauseGroups"]:
            self.assertTrue(set(g["findingIds"]) <= known, g["id"])

    def test_group_ids_are_stable_across_runs(self):
        findings = findings_of("multi_file_correlation")
        a = [g["id"] for g in investigation.build(findings, {})["rootCauseGroups"]]
        b = [g["id"] for g in investigation.build(findings, {})["rootCauseGroups"]]
        self.assertEqual(a, b)

    def test_overall_risk_levels(self):
        self.assertEqual(investigation.build([], {})["overallRisk"], "none")
        low = [{"id": "x", "evidenceClass": "potential", "severity": "low", "riskScore": 4.0, "filePath": "a",
                "startLine": 1, "confidence": 0.5, "category": "loose", "ruleId": "R", "title": "t"}]
        self.assertEqual(investigation.build(low, {})["overallRisk"], "low")
        high = [dict(low[0], evidenceClass="confirmed", severity="critical", riskScore=36.0)]
        self.assertEqual(investigation.build(high, {})["overallRisk"], "high")

    def test_facts_report_coverage_skips_and_parse_failures(self):
        cov = {"filesScanned": 2, "skipped": {"binary": 1}, "parseFailures": ["src/broken.py"], "deduplicated": 2}
        texts = [f["text"] for f in investigation.build(findings_of("malformed_python"), cov)["facts"]]
        self.assertTrue(any("1 file(s) skipped as binary" in t for t in texts))
        self.assertTrue(any("src/broken.py is not valid Python" in t for t in texts))
        self.assertTrue(any("2 overlapping finding(s)" in t for t in texts))

    def test_build_is_deterministic_and_states_its_methodology(self):
        findings = findings_of("multi_language")
        a = investigation.build(findings, {"filesScanned": 2})
        self.assertEqual(a, investigation.build(findings, {"filesScanned": 2}))
        self.assertIn("highest finding risk score", a["methodology"])
        self.assertTrue(a["limitations"])

    def test_components_group_by_top_level_directory(self):
        comps = {c["path"] for c in investigation.build(findings_of("multi_language"), {})["components"]}
        self.assertEqual(comps, {"cmd", "web"})


class FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.sent = None

    def open(self, req, timeout=None):
        self.sent = req
        return _Resp(json.dumps({"content": [{"type": "text", "text": self.reply}]}).encode())


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GroupAITests(unittest.TestCase):
    def setUp(self):
        self.findings = findings_of("multi_file_correlation")
        self.inv = investigation.build(self.findings, {})
        self.group = next(g for g in self.inv["rootCauseGroups"] if g["kind"] == "repeated-pattern")
        self.members = [f for f in self.findings if f["id"] in self.group["findingIds"]]
        self.env = {"NEXUS_AI_API_KEY": "test-key-not-real"}

    def reply(self, **overrides):
        base = {"explanation": "Both stubs have no body, which suggests an unfinished pass.",
                "hypothesis": "The two functions may be waiting on the same missing helper.",
                "confidence": "medium", "verificationSteps": ["Implement both functions and run their callers."]}
        base.update(overrides)
        return json.dumps(base)

    def test_disabled_without_a_key(self):
        with self.assertRaises(ai.AIError) as cm:
            ai.explain_group(self.group, self.members, env={})
        self.assertEqual(cm.exception.code, "ai_disabled")

    def test_valid_group_reply_is_returned_with_notice(self):
        out = ai.explain_group(self.group, self.members, env=self.env, opener=FakeProvider(self.reply()))
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual(out["notice"], ai.NOTICE)

    def test_group_prompt_marks_repository_content_as_untrusted(self):
        provider = FakeProvider(self.reply())
        ai.explain_group(self.group, self.members, env=self.env, opener=provider)
        sent = json.loads(provider.sent.data.decode())["messages"][0]["content"]
        self.assertIn("untrusted repository data", sent)

    def test_reply_citing_a_file_outside_the_group_is_rejected(self):
        bad = self.reply(explanation="The problem also appears in pkg/app.py:1.")
        with self.assertRaises(ai.AIError):
            ai.explain_group(self.group, self.members, env=self.env, opener=FakeProvider(bad))

    def test_reply_citing_a_line_not_in_the_excerpts_is_rejected(self):
        bad = self.reply(explanation="The stub at pkg/reports.py:999 is also empty.")
        with self.assertRaises(ai.AIError):
            ai.explain_group(self.group, self.members, env=self.env, opener=FakeProvider(bad))

    def test_malformed_group_replies_are_rejected(self):
        cases = [self.reply(confidence="certain"), self.reply(hypothesis=""), self.reply(verificationSteps="run it"),
                 json.dumps(["not", "an", "object"]), "not json at all"]
        for text in cases:
            with self.subTest(text=text[:40]):
                with self.assertRaises(ai.AIError):
                    ai.explain_group(self.group, self.members, env=self.env, opener=FakeProvider(text))

    def test_provider_failure_does_not_leak_the_key(self):
        class Broken:
            def open(self, req, timeout=None):
                raise TimeoutError("timed out")

        with self.assertRaises(ai.AIError) as cm:
            ai.explain_group(self.group, self.members, env=self.env, opener=Broken())
        self.assertEqual(cm.exception.code, "ai_unavailable")
        self.assertNotIn("test-key-not-real", cm.exception.message)


class InvestigationApiTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.zip = self._zip({
            "pkg/app.py": "from .models import Order\n",
            "pkg/reports.py": "def build_summary(rows):\n    pass\n",
            "pkg/exports.py": "def build_export(rows):\n    pass\n",
        })
        analysis = service.run_archive_analysis(self.store, "demo", "upload:demo", self.zip,
                                                owner_id=helpers.user_id(self.store))
        self.aid = analysis["id"]

    def tearDown(self):
        self.store.close()

    @staticmethod
    def _zip(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in entries.items():
                zf.writestr(name, text)
        return buf.getvalue()

    def test_investigation_endpoint_returns_groups_and_coverage(self):
        status, headers, data = helpers.handle(self.store, "GET", f"/api/analyses/{self.aid}/investigation")
        self.assertEqual(status, 200)
        body = json.loads(data)
        self.assertTrue(body["investigation"]["deterministic"])
        kinds = {g["kind"] for g in body["investigation"]["rootCauseGroups"]}
        self.assertIn("repeated-pattern", kinds)
        self.assertIn("broken-import", kinds)
        self.assertIn("filesScanned", body["investigation"]["coverage"])
        self.assertFalse(body["ai"]["enabled"])

    def test_another_account_cannot_read_the_investigation(self):
        uid = self.store.create_user("other@example.com", "Other", auth.hash_password("correct-horse-1"))
        token, digest = auth.new_token()
        self.store.create_session(digest, uid, auth.expiry_iso())
        cookie = f"{auth.SESSION_COOKIE}={token}"
        status, _, _ = api.handle(self.store, "GET", f"/api/analyses/{self.aid}/investigation", b"",
                                  {"Cookie": cookie})
        self.assertIn(status, (403, 404))

    def test_group_explain_without_a_key_is_503_and_unknown_group_is_404(self):
        os.environ.pop("NEXUS_AI_API_KEY", None)
        status, _, data = helpers.handle(self.store, "GET", f"/api/analyses/{self.aid}/investigation")
        groups = json.loads(data)["investigation"]["rootCauseGroups"]
        gid = groups[0]["id"]
        status, _, data = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/investigation/groups/{gid}/explain")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["error"]["code"], "ai_disabled")
        status, _, data = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/investigation/groups/RC-missing00/explain")
        self.assertEqual(status, 404)

    def test_completed_analysis_stores_its_coverage(self):
        stored = self.store.get_analysis(self.aid)["coverage"]
        self.assertEqual(stored["ruleCount"], len(R.RULES))
        self.assertEqual(stored["filesScanned"], 3)


class BenchmarkTests(unittest.TestCase):
    def test_score_math(self):
        s = benchmark.score({"a", "b", "c"}, {"b", "c", "d"})
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (2, 1, 1))
        self.assertEqual(s["precision"], 0.6667)
        self.assertEqual(s["recall"], 0.6667)
        self.assertEqual(s["f1"], 0.6667)

    def test_score_is_none_when_undefined(self):
        empty = benchmark.score(set(), set())
        self.assertIsNone(empty["precision"])
        self.assertIsNone(empty["recall"])
        self.assertIsNone(empty["f1"])

    def test_every_labelled_case_matches_and_each_case_is_timed(self):
        doc = benchmark.run_corpus()
        self.assertGreaterEqual(doc["caseCount"], 18)
        self.assertTrue(all(c["exactMatch"] for c in doc["cases"]), [c["case"] for c in doc["cases"] if not c["exactMatch"]])
        self.assertTrue(all(c["seconds"] >= 0 for c in doc["cases"]))
        self.assertEqual(doc["dataset"], "synthetic-labelled-corpus")

    def test_per_category_results_are_reported(self):
        per = benchmark.run_corpus()["perCategory"]
        self.assertTrue({"security", "gap", "loose"} <= set(per))

    def test_large_generated_tree_finds_only_the_planted_issue(self):
        doc = benchmark.run_large(files=300)
        self.assertEqual(doc["dataset"], "synthetic-generated-large")
        self.assertEqual(doc["filesScanned"], 300 + 10)  # modules plus one __init__.py per package
        self.assertEqual(doc["score"]["fp"], 0)
        self.assertEqual(doc["score"]["fn"], 0)

    def test_report_states_external_validation_was_not_run(self):
        doc = benchmark.report(large_files=0)
        self.assertEqual(doc["externalValidation"]["status"], "not run")
        self.assertNotIn("large", doc)
        self.assertEqual(doc["aiProvider"]["enabled"], ai.status()["enabled"])


if __name__ == "__main__":
    unittest.main()
