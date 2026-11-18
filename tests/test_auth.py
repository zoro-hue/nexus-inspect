"""Authentication and authorization tests (in-memory store, no sockets)."""
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from nexus import api, auth
from nexus.store import Store
from tests import helpers
from tests.test_store_api import VULN


def body(data):
    return json.dumps(data).encode()


def signup(store, email=None, password="correct-horse-1", name="Ada"):
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    return api.handle(store, "POST", "/api/auth/signup", body({"email": email, "name": name, "password": password}))


def cookie_from(headers):
    return headers["Set-Cookie"].split(";", 1)[0]


class PasswordTests(unittest.TestCase):
    def test_hash_is_salted_pbkdf2_and_verifies(self):
        stored = auth.hash_password("correct-horse-1")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("correct-horse-1", stored)
        self.assertTrue(auth.verify_password("correct-horse-1", stored))
        self.assertFalse(auth.verify_password("wrong-horse-1", stored))
        self.assertNotEqual(stored, auth.hash_password("correct-horse-1"))  # random salt

    def test_malformed_stored_hash_is_rejected(self):
        self.assertFalse(auth.verify_password("x", "plain"))
        self.assertFalse(auth.verify_password("x", "md5$1$aa$bb"))


class SignupValidationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_invalid_fields_return_422_with_field_messages(self):
        status, _, data = api.handle(self.store, "POST", "/api/auth/signup",
                                     body({"email": "nope", "name": "", "password": "short"}))
        self.assertEqual(status, 422)
        err = json.loads(data)["error"]
        self.assertEqual(err["code"], "validation_failed")
        self.assertEqual(set(err["fields"]), {"email", "name", "password"})

    def test_password_needs_letter_and_digit(self):
        status, _, data = signup(self.store, password="allletters")
        self.assertEqual(status, 422)
        self.assertIn("password", json.loads(data)["error"]["fields"])

    def test_signup_sets_http_only_session_cookie_and_returns_user(self):
        status, headers, data = signup(self.store, email="Ada@Example.com")
        self.assertEqual(status, 201)
        user = json.loads(data)["user"]
        self.assertEqual(user["email"], "ada@example.com")
        self.assertNotIn("password_hash", user)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)

    def test_duplicate_email_is_rejected(self):
        signup(self.store, email="dup@example.com")
        status, _, data = signup(self.store, email="dup@example.com")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)["error"]["code"], "email_taken")

    def test_password_is_never_stored_in_plain_text(self):
        signup(self.store, email="stored@example.com")
        record = self.store.get_user_by_email("stored@example.com")
        self.assertNotIn("correct-horse-1", record["password_hash"])


class SignInTests(unittest.TestCase):
    def setUp(self):
        auth._failures.clear()
        self.store = Store(":memory:")
        self.email = f"login-{uuid.uuid4().hex[:6]}@example.com"
        signup(self.store, email=self.email)

    def tearDown(self):
        self.store.close()

    def test_correct_password_signs_in(self):
        status, headers, data = api.handle(self.store, "POST", "/api/auth/signin",
                                           body({"email": self.email, "password": "correct-horse-1"}))
        self.assertEqual(status, 200)
        self.assertIn("nexus_session=", headers["Set-Cookie"])
        self.assertEqual(json.loads(data)["user"]["email"], self.email)

    def test_wrong_password_and_unknown_email_return_the_same_error(self):
        wrong = api.handle(self.store, "POST", "/api/auth/signin",
                           body({"email": self.email, "password": "wrong-horse-1"}))
        unknown = api.handle(self.store, "POST", "/api/auth/signin",
                             body({"email": "ghost@example.com", "password": "wrong-horse-1"}))
        self.assertEqual(wrong[0], 401)
        self.assertEqual(unknown[0], 401)
        self.assertEqual(json.loads(wrong[2])["error"]["message"], json.loads(unknown[2])["error"]["message"])

    def test_throttle_blocks_after_repeated_failures(self):
        for _ in range(auth.THROTTLE_LIMIT):
            api.handle(self.store, "POST", "/api/auth/signin", body({"email": self.email, "password": "bad-horse-1"}))
        status, _, data = api.handle(self.store, "POST", "/api/auth/signin",
                                     body({"email": self.email, "password": "correct-horse-1"}))
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(data)["error"]["code"], "too_many_attempts")

    def test_missing_fields_are_validation_errors(self):
        status, _, _ = api.handle(self.store, "POST", "/api/auth/signin", body({}))
        self.assertEqual(status, 422)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_me_reports_signed_out_as_null_user(self):
        status, _, data = api.handle(self.store, "GET", "/api/auth/me")
        self.assertEqual((status, json.loads(data)["user"]), (200, None))
        _, headers, _ = signup(self.store)
        me = api.handle(self.store, "GET", "/api/auth/me", b"", {"Cookie": cookie_from(headers)})
        self.assertEqual(json.loads(me[2])["user"]["name"], "Ada")

    def test_signout_invalidates_the_session_server_side(self):
        _, headers, _ = signup(self.store)
        cookie = cookie_from(headers)
        status, out_headers, _ = api.handle(self.store, "POST", "/api/auth/signout", b"", {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", out_headers["Set-Cookie"])
        self.assertIsNone(json.loads(api.handle(self.store, "GET", "/api/auth/me", b"", {"Cookie": cookie})[2])["user"])

    def test_expired_session_is_rejected(self):
        _, headers, _ = signup(self.store)
        token = cookie_from(headers).split("=", 1)[1]
        self.store._db.execute("UPDATE sessions SET expires_at = ?",
                               ((datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        self.store._db.commit()
        self.assertIsNone(self.store.session_user(auth.hash_token(token), auth.now_iso()))
        self.assertIsNone(json.loads(api.handle(self.store, "GET", "/api/auth/me", b"", {"Cookie": f"nexus_session={token}"})[2])["user"])

    def test_session_tokens_are_stored_as_digests(self):
        _, headers, _ = signup(self.store)
        token = cookie_from(headers).split("=", 1)[1]
        digests = [r[0] for r in self.store._db.execute("SELECT token_hash FROM sessions").fetchall()]
        self.assertNotIn(token, digests)
        self.assertIn(auth.hash_token(token), digests)

    def test_forged_cookie_is_rejected(self):
        self.assertIsNone(json.loads(api.handle(self.store, "GET", "/api/auth/me", b"", {"Cookie": "nexus_session=forged"})[2])["user"])


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        _, a_headers, _ = signup(self.store, email="owner-a@example.com")
        _, b_headers, _ = signup(self.store, email="owner-b@example.com")
        self.a = {"Cookie": cookie_from(a_headers)}
        self.b = {"Cookie": cookie_from(b_headers)}

    def tearDown(self):
        self.store.close()

    def test_protected_routes_require_a_session(self):
        for method, path in [("GET", "/api/projects"), ("GET", "/api/analyses"),
                             ("GET", "/api/ai/status"), ("GET", "/api/analyses/ana_missing")]:
            with self.subTest(path=path):
                status, _, data = api.handle(self.store, method, path)
                self.assertEqual(status, 401)
                self.assertEqual(json.loads(data)["error"]["code"], "unauthorized")

    def test_public_routes_stay_public(self):
        self.assertEqual(api.handle(self.store, "GET", "/health")[0], 200)
        self.assertEqual(api.handle(self.store, "GET", "/api/rules")[0], 200)

    def test_users_only_see_their_own_projects_and_analyses(self):
        status, _, data = api.handle(self.store, "POST", "/api/analyses?name=mine", VULN,
                                     {"Content-Type": "application/zip", **self.a})
        self.assertEqual(status, 201)
        aid = json.loads(data)["id"]
        self.assertEqual(len(json.loads(api.handle(self.store, "GET", "/api/projects", b"", self.a)[2])["items"]), 1)
        self.assertEqual(json.loads(api.handle(self.store, "GET", "/api/projects", b"", self.b)[2])["items"], [])
        self.assertEqual(json.loads(api.handle(self.store, "GET", "/api/analyses", b"", self.b)[2])["items"], [])

    def test_other_users_analysis_looks_missing_not_forbidden(self):
        _, _, data = api.handle(self.store, "POST", "/api/analyses?name=mine", VULN,
                                {"Content-Type": "application/zip", **self.a})
        aid = json.loads(data)["id"]
        own = api.handle(self.store, "GET", f"/api/analyses/{aid}", b"", self.a)
        other = api.handle(self.store, "GET", f"/api/analyses/{aid}", b"", self.b)
        self.assertEqual(own[0], 200)
        self.assertEqual(other[0], 404)
        self.assertEqual(json.loads(other[2])["error"]["code"], "not_found")
        for path in [f"/api/analyses/{aid}/findings", f"/api/analyses/{aid}/report?format=json",
                     f"/api/analyses/{aid}/files"]:
            with self.subTest(path=path):
                self.assertEqual(api.handle(self.store, "GET", path, b"", self.b)[0], 404)

    def test_other_users_cannot_change_findings(self):
        _, _, data = api.handle(self.store, "POST", "/api/analyses?name=mine", VULN,
                                {"Content-Type": "application/zip", **self.a})
        aid = json.loads(data)["id"]
        findings = json.loads(api.handle(self.store, "GET", f"/api/analyses/{aid}/findings", b"", self.a)[2])["items"]
        fid = findings[0]["id"]
        patched = api.handle(self.store, "PATCH", f"/api/analyses/{aid}/findings/{fid}",
                             body({"status": "fixed"}), self.b)
        self.assertEqual(patched[0], 404)
        still = self.store.get_finding(aid, fid)
        self.assertEqual(still["status"], "open")

    def test_helper_sessions_do_not_leak_between_stores(self):
        other = Store(":memory:")
        try:
            self.assertNotEqual(helpers.session_cookie(self.store), helpers.session_cookie(other))
        finally:
            other.close()


class ProjectSettingsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        _, a_headers, _ = signup(self.store, email="proj-a@example.com")
        _, b_headers, _ = signup(self.store, email="proj-b@example.com")
        self.a = {"Cookie": cookie_from(a_headers)}
        self.b = {"Cookie": cookie_from(b_headers)}
        _, _, data = api.handle(self.store, "POST", "/api/analyses?name=settings", VULN,
                                {"Content-Type": "application/zip", **self.a})
        self.pid = self.store.get_analysis(json.loads(data)["id"])["projectId"]

    def tearDown(self):
        self.store.close()

    def test_owner_can_read_project_with_its_analyses(self):
        status, _, data = api.handle(self.store, "GET", f"/api/projects/{self.pid}", b"", self.a)
        project = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(len(project["analyses"]), 1)

    def test_owner_can_rename_project(self):
        status, _, data = api.handle(self.store, "PATCH", f"/api/projects/{self.pid}",
                                     body({"name": "  Renamed repo  "}), self.a)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["name"], "Renamed repo")

    def test_other_user_cannot_read_or_rename_project(self):
        self.assertEqual(api.handle(self.store, "GET", f"/api/projects/{self.pid}", b"", self.b)[0], 404)
        self.assertEqual(api.handle(self.store, "PATCH", f"/api/projects/{self.pid}", body({"name": "x"}), self.b)[0], 404)
        self.assertNotEqual(self.store.get_project(self.pid)["name"], "x")

    def test_rename_validates_name(self):
        for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 121}, {"name": 5}, {}):
            with self.subTest(bad=bad):
                self.assertEqual(api.handle(self.store, "PATCH", f"/api/projects/{self.pid}", body(bad), self.a)[0], 422)

    def test_signed_out_cannot_access_project(self):
        self.assertEqual(api.handle(self.store, "GET", f"/api/projects/{self.pid}")[0], 401)
        self.assertEqual(api.handle(self.store, "PATCH", f"/api/projects/{self.pid}", body({"name": "x"}))[0], 401)


if __name__ == "__main__":
    unittest.main()
