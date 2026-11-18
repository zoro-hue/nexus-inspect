"""Regression tests for security controls: rate limits, concurrency cap, cross-site write refusal, error bodies."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid

import server
from nexus import api, service
from nexus.ratelimit import Limiter, RateLimited
from nexus.store import Store
from tests.test_store_api import VULN


def body(d):
    return json.dumps(d).encode()


class RateLimiterTests(unittest.TestCase):
    def test_allows_up_to_limit_then_refuses_with_retry_after(self):
        now = [0.0]
        lim = Limiter(clock=lambda: now[0])
        for _ in range(3):
            lim.check("k", 3, 60)
        with self.assertRaises(RateLimited) as ctx:
            lim.check("k", 3, 60)
        self.assertGreaterEqual(ctx.exception.retry_after, 1)
        now[0] = 61.0
        lim.check("k", 3, 60)  # the window has passed

    def test_keys_are_independent(self):
        lim = Limiter()
        lim.check("a", 1, 60)
        lim.check("b", 1, 60)


class AuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_signup_is_limited_per_client_address(self):
        limit = api.SIGNUP_LIMIT[0]
        statuses = []
        for i in range(limit + 1):
            status, _, _ = api.handle(self.store, "POST", "/api/auth/signup",
                                      body({"email": f"rl{i}@example.com", "name": "R", "password": "correct-horse-1"}),
                                      client_ip="203.0.113.7")
            statuses.append(status)
        self.assertEqual(statuses[:limit], [201] * limit)
        self.assertEqual(statuses[-1], 429)

    def test_signup_limit_does_not_affect_another_address(self):
        for i in range(api.SIGNUP_LIMIT[0]):
            api.handle(self.store, "POST", "/api/auth/signup",
                       body({"email": f"ip1-{i}@example.com", "name": "R", "password": "correct-horse-1"}),
                       client_ip="198.51.100.1")
        status, _, _ = api.handle(self.store, "POST", "/api/auth/signup",
                                  body({"email": "other@example.com", "name": "R", "password": "correct-horse-1"}),
                                  client_ip="198.51.100.2")
        self.assertEqual(status, 201)

    def test_signin_is_limited_per_address_even_across_many_emails(self):
        limit = api.SIGNIN_LIMIT[0]
        last = None
        for i in range(limit + 1):
            last = api.handle(self.store, "POST", "/api/auth/signin",
                              body({"email": f"nobody{i}@example.com", "password": "wrong-horse-1"}),
                              client_ip="192.0.2.9")
        self.assertEqual(last[0], 429)
        self.assertIn("Retry-After", last[1])
        self.assertEqual(json.loads(last[2])["error"]["code"], "rate_limited")

    def test_rate_limited_response_carries_retry_after(self):
        for i in range(api.SIGNUP_LIMIT[0]):
            api.handle(self.store, "POST", "/api/auth/signup",
                       body({"email": f"ra{i}@example.com", "name": "R", "password": "correct-horse-1"}),
                       client_ip="192.0.2.50")
        status, headers, _ = api.handle(self.store, "POST", "/api/auth/signup",
                                        body({"email": "late@example.com", "name": "R", "password": "correct-horse-1"}),
                                        client_ip="192.0.2.50")
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)


class AnalysisCapTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        _, _, data = api.handle(self.store, "POST", "/api/auth/signup",
                                body({"email": "cap@example.com", "name": "C", "password": "correct-horse-1"}))
        self.user_id = json.loads(data)["user"]["id"]
        self.cookie = api.handle(self.store, "POST", "/api/auth/signin",
                                 body({"email": "cap@example.com", "password": "correct-horse-1"}))[1]["Set-Cookie"].split(";", 1)[0]

    def tearDown(self):
        self.store.close()

    def test_concurrent_analyses_per_account_are_capped(self):
        for i in range(api.MAX_ACTIVE_ANALYSES):
            service.start_archive(self.store, f"queued-{i}", "upload:x", self.user_id)  # queued, not yet run
        status, _, data = api.handle(self.store, "POST", "/api/analyses?name=one-too-many&async=1", VULN,
                                     {"Content-Type": "application/zip", "Cookie": self.cookie})
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(data)["error"]["code"], "too_many_active")

    def test_finished_analyses_do_not_count_toward_the_cap(self):
        for i in range(api.MAX_ACTIVE_ANALYSES):
            aid = service.start_archive(self.store, f"done-{i}", "upload:x", self.user_id)
            self.store.update_analysis(aid, status="completed", stage="completed")
        status, _, _ = api.handle(self.store, "POST", "/api/analyses?name=fine", VULN,
                                  {"Content-Type": "application/zip", "Cookie": self.cookie})
        self.assertEqual(status, 201)

    def test_other_accounts_are_not_capped_by_this_one(self):
        for i in range(api.MAX_ACTIVE_ANALYSES):
            service.start_archive(self.store, f"busy-{i}", "upload:x", self.user_id)
        _, _, data = api.handle(self.store, "POST", "/api/auth/signup",
                                body({"email": "free@example.com", "name": "F", "password": "correct-horse-1"}))
        other_cookie = api.handle(self.store, "POST", "/api/auth/signin",
                                  body({"email": "free@example.com", "password": "correct-horse-1"}))[1]["Set-Cookie"].split(";", 1)[0]
        status, _, _ = api.handle(self.store, "POST", "/api/analyses?name=free", VULN,
                                  {"Content-Type": "application/zip", "Cookie": other_cookie})
        self.assertEqual(status, 201)  # runs inline, so no background thread outlives the test


class CrossSiteWriteTests(unittest.TestCase):
    """Real socket: browsers send Origin on cross-site requests; the server must refuse those writes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.httpd, self.store = server.build_server("127.0.0.1", 0, os.path.join(self.tmp, "s.sqlite"))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        email = f"csrf-{uuid.uuid4().hex[:8]}@example.com"
        req = urllib.request.Request(self.base + "/api/auth/signup",
                                     data=body({"email": email, "name": "C", "password": "correct-horse-1"}),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            self.cookie = r.headers["Set-Cookie"].split(";", 1)[0]

    def tearDown(self):
        server.stop_server(self.httpd)
        self.thread.join(timeout=5)
        self.store.close()

    def send(self, method, path, data, headers):
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_cross_site_post_with_session_is_refused(self):
        status, data = self.send("POST", "/api/analyses/url", body({"url": "https://github.com/a/b"}), {
            "Content-Type": "application/json", "Cookie": self.cookie,
            "Origin": "https://evil.example", "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(data)["error"]["code"], "cross_site_request")

    def test_same_origin_post_is_allowed(self):
        status, _ = self.send("POST", "/api/repositories/inspect", body({"url": "not a url"}), {
            "Content-Type": "application/json", "Cookie": self.cookie,
            "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertNotEqual(status, 403)

    def test_request_without_origin_is_not_blocked_by_the_guard(self):
        status, _ = self.send("POST", "/api/repositories/inspect", body({"url": "not a url"}), {
            "Content-Type": "application/json", "Cookie": self.cookie})
        self.assertNotEqual(status, 403)

    def test_cross_site_get_is_not_affected(self):
        status, _ = self.send("GET", "/api/projects", None, {
            "Cookie": self.cookie, "Origin": "https://evil.example"})
        self.assertEqual(status, 200)

    def test_cross_site_post_without_session_is_not_treated_as_csrf(self):
        status, _ = self.send("POST", "/api/auth/signin", body({"email": "x@example.com", "password": "y"}), {
            "Content-Type": "application/json", "Origin": "https://evil.example"})
        self.assertEqual(status, 401)

    def test_signed_in_cross_site_patch_is_refused(self):
        status, data = self.send("PATCH", "/api/projects/prj_missing", body({"name": "x"}), {
            "Content-Type": "application/json", "Cookie": self.cookie,
            "Origin": "https://evil.example", "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 403)


class ErrorDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_wrong_password_response_never_echoes_the_password(self):
        api.handle(self.store, "POST", "/api/auth/signup",
                   body({"email": "echo@example.com", "name": "E", "password": "correct-horse-1"}))
        secret = "Sup3r-secret-guess"
        status, _, data = api.handle(self.store, "POST", "/api/auth/signin",
                                     body({"email": "echo@example.com", "password": secret}))
        self.assertEqual(status, 401)
        self.assertNotIn(secret, data.decode())
        self.assertNotIn("pbkdf2", data.decode())

    def test_user_records_never_expose_password_hashes(self):
        _, _, data = api.handle(self.store, "POST", "/api/auth/signup",
                                body({"email": "hash@example.com", "name": "H", "password": "correct-horse-1"}))
        self.assertNotIn("pbkdf2", data.decode())
        self.assertNotIn("password", json.loads(data)["user"])


if __name__ == "__main__":
    unittest.main()
