import json
import unittest

from nexus import api
from tests import helpers
from nexus.scanner import SEVERITY_WEIGHT  # noqa: F401  (import check)
from nexus.store import Store, StoreError, now_iso
from tests.test_ingest import make_zip

VULN = make_zip([
    ("repo-main/src/app.js", b'const users = require("./db/users");\nconst orders = require("./routes/orders");\nconsole.log(users, orders);\n'),
    ("repo-main/src/db/users.js", b'function f(db, email) {\n  const q = "SELECT * FROM users WHERE email = \'" + email + "\'";\n  return db.query(q);\n}\nmodule.exports = { f };\n'),
    ("repo-main/src/routes/orders.js", b'function r(router) {\n  router.post("/orders", async (req, res) => {\n    const { sku } = req.body;\n    res.json({ sku });\n  });\n}\nmodule.exports = { r };\n'),
    ("repo-main/config/settings.js", b'module.exports = {\n  apiKey: "sk_live_9f8e7d6c5b4a3210",\n};\n'),
])
CLEAN = make_zip([("repo/src/app.js", b'const h = require("./h");\nconsole.log(h);\n'),
                  ("repo/src/h.js", b'function h() {\n  return 1;\n}\nmodule.exports = h;\n')])


def call(store, method, path, body=b"", ctype=None):
    headers = {"Content-Type": ctype} if ctype else {}
    status, hdrs, data = helpers.handle(store, method, path, body, headers)
    ctype_out = hdrs.get("Content-Type", "")
    is_json = ctype_out.startswith("application/json") or ctype_out.startswith("application/sarif+json")
    return status, (json.loads(data) if is_json else data)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_schema_version_is_set(self):
        self.assertEqual(self.store._db.execute("PRAGMA user_version").fetchone()[0], 6)

    def test_status_change_requires_reason_and_keeps_history(self):
        from nexus.service import run_archive_analysis
        a = run_archive_analysis(self.store, "demo", "upload:demo", VULN, owner_id=helpers.user_id(self.store))
        fid = self.store.list_findings(a["id"])[0]["id"]
        with self.assertRaises(StoreError) as ctx:
            self.store.set_finding_status(a["id"], fid, "false_positive", "")
        self.assertEqual(ctx.exception.code, "reason_required")
        self.store.set_finding_status(a["id"], fid, "false_positive", "Test fixture value")
        self.store.set_finding_status(a["id"], fid, "open")
        f = self.store.get_finding(a["id"], fid)
        self.assertEqual([e["to_status"] for e in f["history"]], ["false_positive", "open"])
        self.assertEqual(f["history"][0]["reason"], "Test fixture value")

    def test_invalid_status_rejected(self):
        with self.assertRaises(StoreError):
            self.store.set_finding_status("x", "y", "approved")

    def test_missing_analysis_is_not_found(self):
        with self.assertRaises(StoreError) as ctx:
            self.store.get_analysis("ana_missing")
        self.assertEqual(ctx.exception.status, 404)

    def test_persistence_survives_reopen(self):
        import os, tempfile
        from nexus.service import run_archive_analysis
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.sqlite")
            s1 = Store(path)
            aid = run_archive_analysis(s1, "p", "upload:p", VULN, owner_id=helpers.user_id(s1))["id"]
            n = len(s1.list_findings(aid))
            s1.close()
            s2 = Store(path)
            self.assertEqual(len(s2.list_findings(aid)), n)
            s2.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_health(self):
        status, body = call(self.store, "GET", "/health")
        self.assertEqual((status, body["ok"]), (200, True))

    def test_upload_analysis_end_to_end(self):
        status, a = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        self.assertEqual(status, 201)
        self.assertEqual(a["status"], "completed")
        self.assertEqual(a["progress"], 100)
        status, page = call(self.store, "GET", f"/api/analyses/{a['id']}/findings?severity=high")
        self.assertTrue(all(f["severity"] == "high" for f in page["items"]))
        self.assertGreaterEqual(page["summary"]["total"], 3)
        ids = {f["ruleId"] for f in call(self.store, "GET", f"/api/analyses/{a['id']}/findings")[1]["items"]}
        self.assertTrue({"SEC-SQL-001", "SEC-SECRET-001", "GAP-VALID-001"} <= ids)

    def test_secret_redacted_through_api(self):
        _, a = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        _, page = call(self.store, "GET", f"/api/analyses/{a['id']}/findings?category=security")
        self.assertNotIn("9f8e7d6c5b4a3210", json.dumps(page))

    def test_clean_repo_completes_with_zero_findings(self):
        status, a = call(self.store, "POST", "/api/analyses?name=clean", CLEAN, "application/zip")
        self.assertEqual(status, 201)
        self.assertEqual(call(self.store, "GET", f"/api/analyses/{a['id']}/findings")[1]["summary"]["total"], 0)

    def test_invalid_archive_recorded_as_failed(self):
        status, a = call(self.store, "POST", "/api/analyses?name=bad", b"nope", "application/zip")
        self.assertEqual(status, 422)
        self.assertEqual((a["status"], a["errorCode"]), ("failed", "invalid_archive"))

    def test_malicious_archive_rejected_safely(self):
        evil = make_zip([("../escape.txt", b"x")])
        status, a = call(self.store, "POST", "/api/analyses?name=evil", evil, "application/zip")
        self.assertEqual(status, 422)
        self.assertEqual(a["errorCode"], "unsafe_path")

    def test_wrong_media_type(self):
        status, body = call(self.store, "POST", "/api/analyses", b"{}", "application/json")
        self.assertEqual((status, body["error"]["code"]), (415, "unsupported_media_type"))

    def test_invalid_url_is_client_error(self):
        status, body = call(self.store, "POST", "/api/analyses/url", b'{"url":"https://evil.example/a/b"}', "application/json")
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_url"))

    def test_invalid_json_body(self):
        status, body = call(self.store, "POST", "/api/analyses/url", b"{not json", "application/json")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_json"))

    def test_status_patch_validation(self):
        _, a = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        fid = call(self.store, "GET", f"/api/analyses/{a['id']}/findings")[1]["items"][0]["id"]
        path = f"/api/analyses/{a['id']}/findings/{fid}"
        status, body = self._patch(path, {"status": "bogus"})
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_status"))
        status, body = self._patch(path, {"status": "suppressed"})
        self.assertEqual(status, 400)
        status, body = self._patch(path, {"status": "fixed", "reason": "patched in PR"})
        self.assertEqual((status, body["status"]), (200, "fixed"))

    def _patch(self, path, payload):
        return call(self.store, "PATCH", path, json.dumps(payload).encode(), "application/json")

    def test_reports_in_three_formats(self):
        _, a = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        _, j = call(self.store, "GET", f"/api/analyses/{a['id']}/report?format=json")
        self.assertEqual(j["summary"]["total"], len(j["findings"]))
        status, md = call(self.store, "GET", f"/api/analyses/{a['id']}/report?format=markdown")
        self.assertEqual(status, 200)
        self.assertIn(b"# NEXUS INSPECT report", md)
        status, sarif = call(self.store, "GET", f"/api/analyses/{a['id']}/report?format=sarif")
        self.assertEqual(sarif["version"], "2.1.0")
        results = sarif["runs"][0]["results"]
        self.assertEqual(len(results), j["summary"]["total"])
        self.assertTrue(all(r["level"] in ("error", "warning", "note") for r in results))
        self.assertEqual(call(self.store, "GET", f"/api/analyses/{a['id']}/report?format=pdf")[0], 400)

    def test_unknown_routes(self):
        self.assertEqual(call(self.store, "GET", "/api/nope")[0], 404)
        self.assertEqual(call(self.store, "GET", "/api/analyses/ana_missing")[0], 404)

    def test_findings_filter_validation(self):
        _, a = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        status, body = call(self.store, "GET", f"/api/analyses/{a['id']}/findings?minConfidence=2")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_parameter"))

    def test_scan_diff_comparison(self):
        _, a1 = call(self.store, "POST", "/api/analyses?name=demo", CLEAN, "application/zip")
        _, a2 = call(self.store, "POST", "/api/analyses?name=demo", VULN, "application/zip")
        status, diff = call(self.store, "GET", f"/api/analyses/{a2['id']}/diff?against={a1['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(diff["summary"]["baselineTotal"], 0)
        self.assertGreater(diff["summary"]["currentTotal"], 0)
        self.assertEqual(diff["summary"]["newCount"], diff["summary"]["currentTotal"])
        self.assertEqual(diff["summary"]["resolvedCount"], 0)
        self.assertEqual(diff["summary"]["delta"], diff["summary"]["currentTotal"])
        self.assertEqual(call(self.store, "GET", f"/api/analyses/{a2['id']}/diff")[0], 400)


if __name__ == "__main__":
    unittest.main()
