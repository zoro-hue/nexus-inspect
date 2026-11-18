"""Phase 2 journey: a labelled repository fixture (the corpus, packed as one repository) goes through the real API:
upload, asynchronous analysis, polling, findings compared with the labels, file inventory, and all three exports."""
import io
import json
import os
import tempfile
import time
import unittest
import zipfile

from nexus import api, rules  # noqa: F401  (rules imported so RULES are registered for the report)
from tests import helpers
from nexus.store import Store
from tests.corpus_runner import CORPUS, load_cases, expected_set


def corpus_repository_zip():
    """Pack every corpus case under one wrapper folder, the way GitHub archives are laid out."""
    buf = io.BytesIO()
    expected = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root_name in sorted(os.listdir(CORPUS)):
            case_dir = os.path.join(CORPUS, root_name)
            if not os.path.isdir(case_dir):
                continue
            for dp, _, fn in os.walk(case_dir):
                for name in fn:
                    if name == "expected.json":
                        continue
                    full = os.path.join(dp, name)
                    rel = os.path.relpath(full, CORPUS).replace(os.sep, "/")
                    z.write(full, "demo-repository/" + rel)
        for name, _root, spec in load_cases():
            for rule_id, path, line in expected_set(spec):
                expected.add((rule_id, f"{name}/{path}", line))
    return buf.getvalue(), expected


class JourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.store = Store(os.path.join(cls.dir, "journey.sqlite"))
        cls.archive, cls.expected = corpus_repository_zip()

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    def _run_async(self):
        status, _, body = helpers.handle(self.store, "POST", "/api/analyses?async=1&name=demo-repository",
                                     self.archive, {"Content-Type": "application/zip"})
        self.assertEqual(status, 202)
        aid = json.loads(body)["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            analysis = self.store.get_analysis(aid)
            if analysis["status"] not in ("queued", "running"):
                return analysis
            time.sleep(0.05)
        self.fail("analysis did not finish")

    def test_findings_match_the_labels_exactly(self):
        analysis = self._run_async()
        self.assertEqual(analysis["status"], "completed", analysis.get("errorMessage"))
        findings = self.store.list_findings(analysis["id"])
        actual = {(f["ruleId"], f["filePath"], f["startLine"]) for f in findings}
        missing = self.expected - actual
        unexpected = actual - self.expected
        self.assertFalse(missing, f"labelled findings not reported: {sorted(missing)}")
        self.assertFalse(unexpected, f"unlabelled findings reported: {sorted(unexpected)}")
        self.assertEqual(len(self.expected), len(actual))

    def test_every_finding_has_redacted_evidence_and_a_real_location(self):
        analysis = self._run_async()
        for f in self.store.list_findings(analysis["id"]):
            self.assertTrue(f["excerpt"], f["id"])
            self.assertTrue(any(row["highlight"] and row["line"] == f["startLine"] for row in f["excerpt"]), f["id"])
            self.assertNotIn("sk_live_9f8e7d6c5b4a3210", json.dumps(f))

    def test_file_inventory_matches_the_archive(self):
        analysis = self._run_async()
        paths = {f["path"] for f in self.store.list_files(analysis["id"])}
        self.assertIn("secret_hardcoded/config/settings.js", paths)
        self.assertIn("sql_injection/src/db/users.js", paths)
        self.assertNotIn("sql_injection/expected.json", paths)

    def test_all_three_exports_are_well_formed(self):
        analysis = self._run_async()
        aid = analysis["id"]
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/report?format=json")
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(report["summary"]["total"], len(self.expected))
        status, headers, body = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/report?format=markdown")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers["Content-Type"])
        self.assertIn("# NEXUS INSPECT report", body.decode())
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{aid}/report?format=sarif")
        sarif = json.loads(body)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(len(sarif["runs"][0]["results"]), len(self.expected))

    def test_triage_round_trip_on_the_real_analysis(self):
        analysis = self._run_async()
        aid = analysis["id"]
        finding = self.store.list_findings(aid)[0]
        status, _, body = helpers.handle(self.store, "PATCH", f"/api/analyses/{aid}/findings/{finding['id']}",
                                     json.dumps({"status": "suppressed", "reason": "Demo acceptance"}).encode(),
                                     {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        detail = json.loads(helpers.handle(self.store, "GET", f"/api/analyses/{aid}/findings/{finding['id']}")[2])
        self.assertEqual(detail["status"], "suppressed")
        self.assertEqual(detail["history"][-1]["reason"], "Demo acceptance")


if __name__ == "__main__":
    unittest.main()
