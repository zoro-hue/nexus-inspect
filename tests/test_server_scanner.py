import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server
from nexus import scanner
from nexus.scanner import scan
from tests.test_ingest import make_zip  # noqa: F401


class ScannerLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, data):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)

    def test_time_budget_marks_result_partial(self):
        self.write("src/app.js", b"// TODO: x\nconsole.log(1);\n")
        result = scan(self.root, "t", time_budget=-1)
        self.assertTrue(result.partial)
        self.assertIn("time budget", result.partial_reason)

    def test_file_count_limit_marks_partial(self):
        old = scanner.MAX_FILES
        scanner.MAX_FILES = 2
        try:
            for i in range(5):
                self.write(f"src/f{i}.js", b"console.log(1);\n")
            result = scan(self.root, "t")
            self.assertTrue(result.partial)
            self.assertEqual(result.files_scanned, 2)
        finally:
            scanner.MAX_FILES = old

    def test_oversized_and_binary_files_skipped(self):
        self.write("src/big.js", b"x" * (scanner.MAX_FILE_BYTES + 1))
        self.write("src/blob.js", b"\x00\x01\x02 binary")
        self.write("docs/image.png", b"\x89PNG")
        result = scan(self.root, "t")
        self.assertEqual(result.files_skipped.get("too_large"), 1)
        self.assertEqual(result.files_skipped.get("binary"), 1)
        self.assertEqual(result.files_skipped.get("unsupported_type"), 1)

    def test_symlinks_are_never_followed(self):
        outside = tempfile.TemporaryDirectory()
        try:
            with open(os.path.join(outside.name, "secret.js"), "w") as fh:
                fh.write('const apiKey = "sk_live_9f8e7d6c5b4a3210";\n')
            try:
                os.symlink(os.path.join(outside.name, "secret.js"), os.path.join(self.root, "link.js"))
            except OSError as e:
                raise unittest.SkipTest("symlinks require administrative privilege on Windows") from e
            result = scan(self.root, "t")
            self.assertEqual(result.findings, [])
            self.assertEqual(result.files_skipped.get("symlink"), 1)
        finally:
            outside.cleanup()

    def test_dependency_directories_pruned(self):
        self.write("node_modules/pkg/index.js", b"// TODO: vendor noise\n")
        self.write("src/app.js", b"console.log(1);\n")
        result = scan(self.root, "t")
        self.assertEqual(result.files_scanned, 1)
        self.assertEqual(result.findings, [])

    def test_findings_scored_and_ordered(self):
        self.write("src/app.js", b'const apiKey = "sk_live_9f8e7d6c5b4a3210";\n// TODO: later\n')
        result = scan(self.root, "t")
        scores = [f["riskScore"] for f in result.findings]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(f["relatedFindings"] for f in result.findings))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.store = server.build_server("127.0.0.1", 0, ":memory:")
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def sign_up(self):
        """Create an account over HTTP and return the Cookie header value from Set-Cookie."""
        import uuid
        payload = json.dumps({"email": f"socket-{uuid.uuid4().hex[:8]}@example.com", "name": "Socket",
                              "password": "correct-horse-1"}).encode()
        req = urllib.request.Request(self.base + "/api/auth/signup", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            set_cookie = r.headers["Set-Cookie"]
        return set_cookie.split(";", 1)[0]

    def test_health_over_socket(self):
        status, headers, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_frontend_served(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"NEXUS", body)

    def test_static_traversal_blocked(self):
        for path in ["/../server.py", "/..%2Fserver.py", "/nexus/api.py", "/.git/config", "/tests/test_api.py"]:
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"import ", body)

    def test_upload_over_socket(self):
        from tests.test_store_api import VULN
        cookie = self.sign_up()
        req = urllib.request.Request(self.base + "/api/analyses?name=socket-demo", data=VULN,
                                     headers={"Content-Type": "application/zip", "Cookie": cookie}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            analysis = json.loads(r.read())
        self.assertEqual(analysis["status"], "completed")
        req = urllib.request.Request(self.base + f"/api/analyses/{analysis['id']}/findings", headers={"Cookie": cookie})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        self.assertEqual(r.status, 200)
        self.assertGreater(json.loads(body)["summary"]["total"], 0)

    def test_oversized_json_body_rejected(self):
        req = urllib.request.Request(self.base + "/api/analyses/url", data=b"x" * (70 * 1024),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 413)


if __name__ == "__main__":
    unittest.main()
