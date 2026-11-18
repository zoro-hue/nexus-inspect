import json
import unittest

from nexus import api, assistant, auth, store


class TestAssistant(unittest.TestCase):
    def setUp(self):
        self.store = store.Store(":memory:")
        self.user_id = self.store.create_user("alice@example.com", "Alice", auth.hash_password("pw123456"))
        self.other_user_id = self.store.create_user("bob@example.com", "Bob", auth.hash_password("pw123456"))
        self.user = {"id": self.user_id, "email": "alice@example.com", "name": "Alice"}
        self.other_user = {"id": self.other_user_id, "email": "bob@example.com", "name": "Bob"}

        # Create session cookies
        token_a, digest_a = auth.new_token()
        self.store.create_session(digest_a, self.user_id, auth.expiry_iso())
        self.headers_alice = {"Cookie": f"{auth.SESSION_COOKIE}={token_a}"}

        token_b, digest_b = auth.new_token()
        self.store.create_session(digest_b, self.other_user_id, auth.expiry_iso())
        self.headers_bob = {"Cookie": f"{auth.SESSION_COOKIE}={token_b}"}

        # Create dummy project and analysis
        self.pid = self.store.create_project("test-repo", "upload:test-repo", self.user_id)
        self.aid = self.store.create_analysis(self.pid)
        self.store.update_analysis(self.aid, status="completed", languages={"python": 100}, coverage={"files": 5})

        # Create findings
        self.store.save_findings(self.aid, [{
            "id": "f_sec_1",
            "ruleId": "SEC-KEY-001",
            "category": "security",
            "title": "Hardcoded API key detected",
            "severity": "critical",
            "confidence": 0.95,
            "filePath": "src/config.py",
            "startLine": 12,
            "endLine": 12,
            "evidence": 'API_KEY = "sk-live-12345"',
            "explanation": "Found hardcoded token.",
            "impact": "Exposed API key can allow unauthorized access.",
            "remediation": "Use environment variables instead of hardcoded secrets.",
            "verificationSteps": ["Check env"],
            "relatedFindings": [],
            "detectionMethod": "pattern",
            "riskScore": 42,
            "createdAt": store.now_iso(),
            "evidenceClass": "confirmed",
            "status": "open",
            "excerpt": [{"line": 12, "text": 'API_KEY = "sk-live-12345"'}],
        }])

    def tearDown(self):
        self.store.close()

    def test_suggestions(self):
        self.assertIn("What rules does NEXUS check?", assistant.get_suggestions("home"))
        self.assertIn("How do I export SARIF for GitHub Code Scanning?", assistant.get_suggestions("reports", True))
        self.assertIn("Summarize this repository's health", assistant.get_suggestions("overview", True))

    def test_guest_chat_rules(self):
        status, headers, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({"message": "What rules does NEXUS check?", "context": {"route": "home"}}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("reply", data)
        self.assertIn("deterministic detection rules", data["reply"])
        self.assertTrue(data.get("fallback"))

    def test_guest_chat_slop_and_loose(self):
        status, headers, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({"message": "Explain coding gaps vs loose ends", "context": {"route": "docs"}}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("Coding Gaps", data["reply"])
        self.assertIn("Loose Ends", data["reply"])

    def test_authenticated_chat_health_summary(self):
        status, _, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({
                "message": "Summarize this repository health and status",
                "context": {"route": "overview", "analysisId": self.aid},
            }).encode("utf-8"),
            headers=self.headers_alice,
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("Repository Health Summary", data["reply"])
        self.assertIn("42/100", data["reply"])
        self.assertTrue("critical" in data["reply"].lower())

    def test_authenticated_chat_specific_finding(self):
        status, _, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({
                "message": "How do I fix this finding?",
                "context": {"route": "findings", "analysisId": self.aid, "findingId": "f_sec_1"},
            }).encode("utf-8"),
            headers=self.headers_alice,
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("SEC-KEY-001", data["reply"])
        self.assertIn("src/config.py:12", data["reply"])

    def test_analysis_ownership_isolation(self):
        # Bob tries to query Alice's analysis
        status, _, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({
                "message": "Summarize health",
                "context": {"route": "overview", "analysisId": self.aid},
            }).encode("utf-8"),
            headers=self.headers_bob,
        )
        self.assertEqual(status, 404)

    def test_unauthenticated_analysis_blocked(self):
        status, _, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({
                "message": "Summarize health",
                "context": {"route": "overview", "analysisId": self.aid},
            }).encode("utf-8"),
        )
        self.assertEqual(status, 401)

    def test_empty_message_validation(self):
        status, _, body = api.handle(
            self.store,
            "POST",
            "/api/ai/chat",
            json.dumps({"message": "   ", "context": {}}).encode("utf-8"),
        )
        self.assertEqual(status, 400)

    def test_suggestions_api(self):
        status, _, body = api.handle(
            self.store,
            "GET",
            "/api/ai/assistant/suggestions?route=overview",
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("suggestions", data)
        self.assertTrue(len(data["suggestions"]) > 0)


if __name__ == "__main__":
    unittest.main()
