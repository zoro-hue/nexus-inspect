"""The API reference must describe the real API: every catalogued endpoint routes, and every error code it shows exists."""
import glob
import json
import os
import re
import unittest

from nexus import api
from nexus.catalog import ENDPOINTS, NONE
from nexus.store import Store
from tests import helpers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def concrete(path):
    return (re.sub(r"\{(analysisId|projectId)\}", "ana_missing", path)
            .replace("{findingId}", "NX-missing").replace("{groupId}", "RC-missing"))


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_reference_route_serves_the_catalog_without_a_session(self):
        status, _, data = api.handle(self.store, "GET", "/api/reference")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)["items"]), len(ENDPOINTS))

    def test_each_documented_method_and_path_exists_in_the_router(self):
        # Streaming routes are handled by the HTTP server before the JSON API router; check their source instead.
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as fh:
            server_source = fh.read()
        for ep in ENDPOINTS:
            with self.subTest(endpoint=f"{ep['method']} {ep['path']}"):
                if ep["path"] == "/api/events":
                    self.assertIn('"/api/events"', server_source)
                    continue
                headers = {} if ep["auth"] == NONE else None
                if headers is None:
                    status, _, data = helpers.handle(self.store, ep["method"], concrete(ep["path"]), b"{}",
                                                     {"Content-Type": "application/json"})
                else:
                    status, _, data = api.handle(self.store, ep["method"], concrete(ep["path"]), b"{}",
                                                 {"Content-Type": "application/json"})
                message = json.loads(data).get("error", {}).get("message") if status >= 400 else None
                self.assertNotEqual(message, "No such endpoint.", f"{ep['method']} {ep['path']} is not routed")

    def test_documented_pairs_are_unique(self):
        pairs = [(e["method"], e["path"]) for e in ENDPOINTS]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_every_documented_error_code_exists_in_the_source(self):
        parts = []
        for f in glob.glob(os.path.join(ROOT, "nexus", "*.py")):
            with open(f, encoding="utf-8") as fh:
                parts.append(fh.read())
        source = "".join(parts)
        codes = set()
        for ep in ENDPOINTS:
            for r in ep["responses"]:
                codes.update(re.findall(r'"code": "([a-z_]+)"', json.dumps(r["example"])))
        missing = sorted(c for c in codes if f'"{c}"' not in source)
        self.assertEqual(missing, [])

    def test_unauthenticated_access_to_protected_endpoints_is_401(self):
        for ep in ENDPOINTS:
            if ep["auth"] == NONE or ep["path"] == "/api/ai/status":
                continue
            with self.subTest(endpoint=f"{ep['method']} {ep['path']}"):
                status, _, _ = api.handle(self.store, ep["method"], concrete(ep["path"]), b"{}")
                self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
