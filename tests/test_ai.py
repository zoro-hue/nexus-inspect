"""Optional AI layer: disabled without a key, redaction before external calls, strict output validation."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from nexus import ai, api, service
from tests import helpers
from nexus.store import Store

SECRET = "abcdef1234567890XYZ"
REPO = {"demo/config/settings.js": f"module.exports = {{\n  api_key: '{SECRET}',\n}};\n"}


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProvider:
    """Records the outgoing request and returns a canned model reply."""

    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.sent = None

    def open(self, req, timeout=None):
        self.sent = req
        body = {"content": [{"type": "text", "text": self.reply_text}]}
        return FakeResponse(json.dumps(body).encode())


class AITests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.sqlite"))
        analysis = service.run_archive_analysis(self.store, "demo", "upload:demo", make_zip(REPO), owner_id=helpers.user_id(self.store))
        self.aid = analysis["id"]
        self.finding = next(f for f in self.store.list_findings(self.aid) if f["ruleId"] == "SEC-SECRET-001")
        self.env = {"NEXUS_AI_API_KEY": "test-key-not-real"}

    def tearDown(self):
        self.store.close()

    def good_reply(self, **overrides):
        reply = {"explanation": "A credential is committed to configuration.",
                 "falsePositiveRisk": "low",
                 "verificationSteps": ["Confirm the value is live.", "Move it to the environment."]}
        reply.update(overrides)
        return json.dumps(reply)

    def test_disabled_without_key_and_reports_why(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, _, body = helpers.handle(self.store, "GET", "/api/ai/status")
            self.assertEqual(status, 200)
            info = json.loads(body)
            self.assertFalse(info["enabled"])
            self.assertIn("NEXUS_AI_API_KEY", info["reason"])
            status, _, body = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain")
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "ai_disabled")

    def test_status_never_exposes_the_key(self):
        with mock.patch.dict(os.environ, {"NEXUS_AI_API_KEY": "sk-ant-very-secret-value"}, clear=True):
            status, _, body = helpers.handle(self.store, "GET", "/api/ai/status")
            self.assertTrue(json.loads(body)["enabled"])
            self.assertNotIn("sk-ant-very-secret-value", body.decode())

    def test_prompt_is_redacted_before_it_leaves_the_server(self):
        provider = FakeProvider(self.good_reply())
        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, body = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                         opener=provider)
        self.assertEqual(status, 200)
        sent = provider.sent.data.decode()
        self.assertNotIn(SECRET, sent)
        self.assertIn("(length", sent)
        self.assertNotIn("test-key-not-real", sent)  # the key travels in a header, never in the body
        self.assertEqual(provider.sent.get_header("X-api-key"), "test-key-not-real")

    def test_valid_reply_is_returned_with_notice(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, body = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                         opener=FakeProvider(self.good_reply()))
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["falsePositiveRisk"], "low")
        self.assertIn("redacted excerpt", result["notice"])

    def test_reply_citing_another_file_is_rejected(self):
        reply = self.good_reply(explanation="See src/other.js:2 for the same problem.")
        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, body = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                         opener=FakeProvider(reply))
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "ai_validation_failed")

    def test_reply_citing_a_line_outside_the_excerpt_is_rejected(self):
        reply = self.good_reply(verificationSteps=[f"Check {self.finding['filePath']}:999 again."])
        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, _ = helpers.handle(self.store, "POST",
                                      f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                      opener=FakeProvider(reply))
        self.assertEqual(status, 422)

    def test_malformed_reply_shapes_are_rejected(self):
        for bad in ["not json at all",
                    json.dumps({"explanation": "x", "falsePositiveRisk": "maybe", "verificationSteps": []}),
                    json.dumps({"explanation": "x", "falsePositiveRisk": "low", "verificationSteps": "one"}),
                    json.dumps({"explanation": "", "falsePositiveRisk": "low", "verificationSteps": []})]:
            with self.subTest(bad=bad[:30]), mock.patch.dict(os.environ, self.env, clear=True):
                status, _, _ = helpers.handle(self.store, "POST",
                                          f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                          opener=FakeProvider(bad))
                self.assertEqual(status, 422)

    def test_fenced_json_reply_is_accepted(self):
        reply = "```json\n" + self.good_reply() + "\n```"
        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, _ = helpers.handle(self.store, "POST",
                                      f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                      opener=FakeProvider(reply))
        self.assertEqual(status, 200)

    def test_provider_failure_does_not_leak_the_key(self):
        class Broken:
            def open(self, req, timeout=None):
                raise OSError("network down: key test-key-not-real")

        with mock.patch.dict(os.environ, self.env, clear=True):
            status, _, body = helpers.handle(self.store, "POST",
                                         f"/api/analyses/{self.aid}/findings/{self.finding['id']}/explain",
                                         opener=Broken())
        self.assertEqual(status, 502)
        self.assertNotIn("test-key-not-real", body.decode())


if __name__ == "__main__":
    unittest.main()
