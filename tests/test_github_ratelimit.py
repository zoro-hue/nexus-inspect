"""Tests for GitHub API Rate Limiting, Authenticated Access, and Git Fallback.

Covers:
1. Public repository with a valid token.
2. Public repository without a token.
3. GitHub API rate-limit response.
4. Successful Git fallback.
5. Invalid repository URL.
6. Nonexistent repository.
7. Private repository without permission.
8. Invalid or insufficient token.
9. Network timeout.
10. Branch listing and branch selection.
11. Repeated Check clicks and caching.
12. ZIP upload and analysis still working.
"""
import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from nexus import api, ingest, service
from nexus.ingest import IngestError
from nexus.store import Store
from tests import helpers
from tests.test_ingest import make_zip

MOCK_REPO = [
    ("repo/app.py", b"import os\nprint('hello')\n"),
    ("repo/README.md", b"# Test Repo\n"),
]


class MockGitHubOpener:
    """Mock urllib opener simulating GitHub REST API and download responses."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.requests = []
        self.addheaders = []

    def open(self, target, timeout=None):
        if isinstance(target, urllib.request.Request):
            url = target.full_url
            hdrs = dict(target.headers)
            hdrs.update({k: v for k, v in target.unredirected_hdrs.items()})
        else:
            url = str(target)
            hdrs = dict(self.addheaders)

        self.requests.append({"url": url, "headers": hdrs, "timeout": timeout})

        for prefix, spec in self.responses.items():
            if url.startswith(prefix):
                status, body, resp_hdrs = spec
                raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                if status >= 400:
                    fp = io.BytesIO(raw)
                    raise urllib.error.HTTPError(url, status, "HTTP Error", resp_hdrs, fp)

                class MockResponse:
                    def __init__(self, data, h):
                        self._data = data
                        self.headers = h

                    def read(self, n=-1):
                        return self._data

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        pass

                return MockResponse(raw, resp_hdrs)

        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}'))


class GitHubRateLimitAndFallbackTests(unittest.TestCase):
    def setUp(self):
        ingest.clear_inspect_cache()
        self.orig_token = os.environ.get("GITHUB_TOKEN")
        if "GITHUB_TOKEN" in os.environ:
            del os.environ["GITHUB_TOKEN"]

    def tearDown(self):
        ingest.clear_inspect_cache()
        if self.orig_token is not None:
            os.environ["GITHUB_TOKEN"] = self.orig_token
        elif "GITHUB_TOKEN" in os.environ:
            del os.environ["GITHUB_TOKEN"]

    # 1. Public repository with a valid token
    def test_01_public_repo_with_valid_token(self):
        os.environ["GITHUB_TOKEN"] = "ghp_secretValidToken12345"
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/project/branches": (200, [{"name": "main"}, {"name": "dev"}], {}),
            "https://api.github.com/repos/acme/project": (200, {"default_branch": "main"}, {}),
        })

        info = ingest.inspect_repository("https://github.com/acme/project", opener=opener)
        self.assertEqual(info["owner"], "acme")
        self.assertEqual(info["repo"], "project")
        self.assertEqual(info["defaultBranch"], "main")
        self.assertEqual(info["branches"], ["main", "dev"])

        # Check Authorization header was sent
        auth_headers = [r["headers"].get("Authorization") for r in opener.requests if "Authorization" in r["headers"]]
        self.assertTrue(len(auth_headers) > 0)
        self.assertEqual(auth_headers[0], "Bearer ghp_secretValidToken12345")

        # Ensure token is never exposed in returned response dict
        self.assertNotIn("token", json.dumps(info).lower())
        self.assertNotIn("ghp_secret", json.dumps(info))

    # 2. Public repository without a token
    def test_02_public_repo_without_token(self):
        if "GITHUB_TOKEN" in os.environ:
            del os.environ["GITHUB_TOKEN"]

        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/public-repo/branches": (200, [{"name": "main"}], {}),
            "https://api.github.com/repos/acme/public-repo": (200, {"default_branch": "main"}, {}),
        })

        info = ingest.inspect_repository("https://github.com/acme/public-repo", opener=opener)
        self.assertEqual(info["owner"], "acme")
        self.assertEqual(info["repo"], "public-repo")
        self.assertEqual(info["defaultBranch"], "main")

        # Verify no Authorization header was sent
        for req in opener.requests:
            self.assertNotIn("Authorization", req["headers"])

    # 3. GitHub API rate-limit response
    def test_03_github_api_rate_limit_response(self):
        rl_headers = {
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1794994690",
            "Retry-After": "45",
        }
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/rate-limited": (
                403,
                {"message": "API rate limit exceeded for 1.2.3.4. (But here's the good news: Authenticated requests get a higher rate limit.)"},
                rl_headers,
            )
        })

        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/rate-limited", opener=opener, fallback=False)

        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(ctx.exception.retry_after, 45)
        self.assertIn("rate limit reached", ctx.exception.message.lower())

    # 4. Successful Git fallback
    def test_04_successful_git_fallback(self):
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/fallback-repo": (
                403,
                {"message": "API rate limit exceeded"},
                {"X-RateLimit-Remaining": "0", "Retry-After": "60"},
            )
        })

        # Mock git_ls_remote to simulate successful Git transport
        mock_git_result = {
            "owner": "acme",
            "repo": "fallback-repo",
            "defaultBranch": "main",
            "branches": ["main", "staging", "v1.0"],
        }
        with mock.patch("nexus.ingest.git_ls_remote", return_value=mock_git_result) as mock_ls:
            info = ingest.inspect_repository("https://github.com/acme/fallback-repo", opener=opener, fallback=True)
            mock_ls.assert_called_once_with("https://github.com/acme/fallback-repo")
            self.assertEqual(info["defaultBranch"], "main")
            self.assertEqual(info["branches"], ["main", "staging", "v1.0"])

    # 5. Invalid repository URL
    def test_05_invalid_repository_url(self):
        invalid_urls = [
            "",
            "   ",
            "https://gitlab.com/owner/repo",
            "https://github.com/../malicious/repo",
            "https://github.com/owner/repo/blob/main/index.js",
            "http://github.com/owner/repo",
            "ftp://github.com/owner/repo",
            "not a url",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(IngestError) as ctx:
                    ingest.parse_github_url(url)
                self.assertEqual(ctx.exception.code, "invalid_url")

    # 6. Nonexistent repository
    def test_06_nonexistent_repository(self):
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/nonexistent": (
                404,
                {"message": "Not Found"},
                {},
            )
        })
        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/nonexistent", opener=opener)
        self.assertEqual(ctx.exception.code, "repository_not_found")
        self.assertEqual(ctx.exception.status, 404)

    # 7. Private repository without permission
    def test_07_private_repo_without_permission(self):
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/private-project": (
                403,
                {"message": "Resource not accessible by integration"},
                {},
            )
        })
        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/private-project", opener=opener, fallback=False)
        self.assertIn(ctx.exception.code, ("private_repository", "insufficient_permissions"))
        self.assertEqual(ctx.exception.status, 403)

    # 8. Invalid or insufficient token
    def test_08_invalid_or_insufficient_token(self):
        # Case A: 401 Bad credentials
        opener_401 = MockGitHubOpener({
            "https://api.github.com/repos/acme/proj": (
                401,
                {"message": "Bad credentials"},
                {},
            )
        })
        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/proj", opener=opener_401)
        self.assertEqual(ctx.exception.code, "invalid_token")
        self.assertEqual(ctx.exception.status, 401)

        # Case B: 403 Insufficient permissions
        opener_403 = MockGitHubOpener({
            "https://api.github.com/repos/acme/proj": (
                403,
                {"message": "Resource not accessible by personal access token (permissions: read)"},
                {},
            )
        })
        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/proj", opener=opener_403, fallback=False)
        self.assertEqual(ctx.exception.code, "insufficient_permissions")
        self.assertEqual(ctx.exception.status, 403)

    # 9. Network timeout
    def test_09_network_timeout(self):
        class TimeoutOpener:
            def open(self, target, timeout=None):
                raise socket.timeout("timed out")

        with self.assertRaises(IngestError) as ctx:
            ingest.inspect_repository("https://github.com/acme/timeout-repo", opener=TimeoutOpener())
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(ctx.exception.status, 504)

    # 10. Branch listing and branch selection
    def test_10_branch_listing_and_branch_selection(self):
        branches_data = [
            {"name": "main"},
            {"name": "feature/awesome"},
            {"name": "v2.0-release"},
            {"name": "hotfix/123"},
        ]
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/branches-test/branches": (200, branches_data, {}),
            "https://api.github.com/repos/acme/branches-test": (200, {"default_branch": "main"}, {}),
        })
        info = ingest.inspect_repository("https://github.com/acme/branches-test", opener=opener)
        self.assertEqual(info["defaultBranch"], "main")
        self.assertEqual(len(info["branches"]), 4)
        self.assertIn("feature/awesome", info["branches"])
        self.assertIn("v2.0-release", info["branches"])

        # Validate valid and invalid branch selections
        self.assertEqual(ingest.validate_branch("feature/awesome"), "feature/awesome")
        self.assertEqual(ingest.validate_branch("v2.0-release"), "v2.0-release")
        self.assertEqual(ingest.validate_branch(""), "HEAD")

        with self.assertRaises(IngestError):
            ingest.validate_branch("../evil")
        with self.assertRaises(IngestError):
            ingest.validate_branch("main;rm -rf /")

    # 11. Repeated Check clicks and caching
    def test_11_repeated_check_clicks_and_caching(self):
        opener = MockGitHubOpener({
            "https://api.github.com/repos/acme/cached-test/branches": (200, [{"name": "main"}], {}),
            "https://api.github.com/repos/acme/cached-test": (200, {"default_branch": "main"}, {}),
        })

        # When opener is None, caching is active
        with mock.patch("nexus.ingest.urllib.request.build_opener", return_value=opener):
            # First call: hits opener
            info1 = ingest.inspect_repository("https://github.com/acme/cached-test", opener=None)
            self.assertEqual(len(opener.requests), 2)  # base repo + branches

            # Second call: served from in-memory cache, no new requests made
            info2 = ingest.inspect_repository("https://github.com/acme/cached-test", opener=None)
            self.assertEqual(len(opener.requests), 2)  # Still 2 requests!
            self.assertEqual(info1, info2)

    # 12. ZIP upload and analysis still working
    def test_12_zip_upload_and_analysis_still_working(self):
        store = Store(":memory:")
        zip_bytes = make_zip(MOCK_REPO)
        analysis = service.run_archive_analysis(
            store,
            project_name="zip-test",
            source="upload:zip-test.zip",
            archive=zip_bytes,
            time_budget=30.0,
        )

        self.assertEqual(analysis["status"], "completed")
        self.assertEqual(analysis["stage"], "report_generation")
        self.assertTrue(analysis.get("filesScanned", 0) >= 1)

        # Ensure findings, edges, and files are saved
        findings = store.list_findings(analysis["id"])
        self.assertIsInstance(findings, list)

        # Ensure report generation works
        rep = store.get_analysis(analysis["id"])
        self.assertEqual(rep["status"], "completed")


if __name__ == "__main__":
    unittest.main()
