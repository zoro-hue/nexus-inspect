"""Phase 1 backend behaviour: redacted excerpts, file inventory, async analysis, cancellation, branch inspection."""
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import zipfile

from nexus import api, ingest, rules, service
from tests import helpers
from nexus.store import Store

SECRET = "abcdef1234567890XYZ"


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return buf.getvalue()


REPO = {
    "demo/config/settings.js": f"module.exports = {{\n  api_key: '{SECRET}',\n  debug: true,\n}};\n",
    "demo/src/app.js": "// TODO: wire up the cache\nconsole.log('hi');\n",
    "demo/package.json": '{"name":"demo","dependencies":{"lodash":"4.17.21"}}\n',
}


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records requested URLs and returns canned bodies; unknown URLs return 404."""

    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def open(self, url, timeout=None):
        self.requested.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode())
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


class RedactionAndInventoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.sqlite"))
        self.analysis = service.run_archive_analysis(self.store, "demo", "upload:demo", make_zip(REPO), time_budget=30, owner_id=helpers.user_id(self.store))

    def tearDown(self):
        self.store.close()

    def test_secret_value_never_appears_in_any_stored_field(self):
        findings = self.store.list_findings(self.analysis["id"])
        self.assertTrue(findings)
        blob = json.dumps(findings)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn(SECRET[3:], blob)

    def test_excerpt_is_numbered_and_highlights_the_finding_line(self):
        secret = next(f for f in self.store.list_findings(self.analysis["id"]) if f["ruleId"] == "SEC-SECRET-001")
        highlighted = [row for row in secret["excerpt"] if row["highlight"]]
        self.assertEqual([row["line"] for row in highlighted], [secret["startLine"]])
        self.assertTrue(all(row["line"] >= 1 for row in secret["excerpt"]))

    def test_redact_line_masks_secret_values(self):
        redacted = rules.redact_line(f"api_key: '{SECRET}',")
        self.assertNotIn(SECRET, redacted)
        self.assertIn("(length", redacted)

    def test_file_inventory_is_saved_and_listed(self):
        files = self.store.list_files(self.analysis["id"])
        paths = [f["path"] for f in files]
        self.assertIn("config/settings.js", paths)
        self.assertTrue(all(f["lines"] >= 1 and f["size"] > 0 for f in files))

    def test_related_finding_details_are_resolved(self):
        secret = next(f for f in self.store.list_findings(self.analysis["id"]) if f["ruleId"] == "SEC-SECRET-001")
        status, _, body = helpers.handle(self.store, "GET", f"/api/analyses/{self.analysis['id']}/findings/{secret['id']}")
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertIn("relatedDetails", detail)
        self.assertEqual(len(detail["relatedDetails"]), len(detail["relatedFindings"]))


class AsyncAndCancelTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.sqlite"))

    def tearDown(self):
        self.store.close()

    def _wait(self, aid, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            analysis = self.store.get_analysis(aid)
            if analysis["status"] not in ("queued", "running"):
                return analysis
            time.sleep(0.02)
        self.fail("analysis did not finish in time")

    def test_async_upload_returns_202_and_completes_in_background(self):
        status, _, body = helpers.handle(self.store, "POST", "/api/analyses?async=1&name=demo",
                                     make_zip(REPO), {"Content-Type": "application/zip"})
        self.assertEqual(status, 202)
        aid = json.loads(body)["id"]
        self.assertEqual(self._wait(aid)["status"], "completed")
        self.assertTrue(self.store.list_findings(aid))

    def test_cancel_before_execution_marks_analysis_cancelled(self):
        aid = service.start_archive(self.store, "demo", "upload:demo", owner_id=helpers.user_id(self.store))
        service.request_cancel(aid)
        analysis = service.execute_archive(self.store, aid, make_zip(REPO))
        self.assertEqual(analysis["status"], "cancelled")
        self.assertEqual(analysis["errorCode"], "cancelled")

    def test_cancel_endpoint_rejects_finished_analysis(self):
        analysis = service.run_archive_analysis(self.store, "demo", "upload:demo", make_zip(REPO), owner_id=helpers.user_id(self.store))
        status, _, body = helpers.handle(self.store, "POST", f"/api/analyses/{analysis['id']}/cancel")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "not_cancellable")

    def test_url_validation_failure_creates_no_record(self):
        status, _, body = helpers.handle(self.store, "POST", "/api/analyses/url?async=1",
                                     json.dumps({"url": "https://example.com/x/y"}).encode(),
                                     {"Content-Type": "application/json"})
        self.assertEqual(status, 422)
        self.assertEqual(self.store.list_projects(), [])


class BranchAndRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.sqlite"))

    def tearDown(self):
        self.store.close()

    def test_inspect_returns_default_branch_and_branches(self):
        opener = FakeOpener({
            "https://api.github.com/repos/acme/app/branches": [{"name": "main"}, {"name": "dev"}],
            "https://api.github.com/repos/acme/app": {"default_branch": "main"},
        })
        info = ingest.inspect_repository("https://github.com/acme/app", opener=opener)
        self.assertEqual(info, {"owner": "acme", "repo": "app", "defaultBranch": "main", "branches": ["main", "dev"]})

    def test_inspect_via_api_reports_missing_repository(self):
        status, _, body = helpers.handle(self.store, "POST", "/api/repositories/inspect",
                                     json.dumps({"url": "https://github.com/acme/missing"}).encode(),
                                     {"Content-Type": "application/json"}, opener=FakeOpener({}))
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "repository_not_found")

    def test_default_branch_downloads_from_head_not_a_branch_named_head(self):
        archive = make_zip(REPO)
        opener = FakeOpener({"https://codeload.github.com/acme/app/zip/HEAD": archive})
        ingest.download_repository_archive("https://github.com/acme/app", "", opener=opener)
        self.assertEqual(opener.requested, ["https://codeload.github.com/acme/app/zip/HEAD"])

    def test_named_branch_uses_refs_heads(self):
        opener = FakeOpener({"https://codeload.github.com/acme/app/zip/refs/heads/dev": make_zip(REPO)})
        ingest.download_repository_archive("https://github.com/acme/app", "dev", opener=opener)
        self.assertTrue(opener.requested[0].endswith("/zip/refs/heads/dev"))

    def test_rules_endpoint_lists_every_rule(self):
        status, _, body = helpers.handle(self.store, "GET", "/api/rules")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["items"]), len(rules.RULES))


if __name__ == "__main__":
    unittest.main()
