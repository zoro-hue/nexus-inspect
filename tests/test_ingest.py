import io
import os
import stat
import tempfile
import unittest
import zipfile

from nexus import ingest
from nexus.ingest import IngestError


def make_zip(entries):
    """entries: list of (name, bytes) or (name, bytes, unix_mode)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            name, data = e[0], e[1]
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if len(e) == 3:
                info.external_attr = e[2] << 16
            zf.writestr(info, data)
    return buf.getvalue()


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def assertRejects(self, data, code):
        with self.assertRaises(IngestError) as ctx:
            ingest.extract_zip(data, self.dest)
        self.assertEqual(ctx.exception.code, code)

    def test_valid_archive_strips_single_wrapper_folder(self):
        root = ingest.extract_zip(make_zip([("repo-main/src/app.js", b"x"), ("repo-main/README.md", b"y")]), self.dest)
        self.assertTrue(root.endswith("repo-main"))
        self.assertTrue(os.path.isfile(os.path.join(root, "src", "app.js")))

    def test_parent_directory_segment_rejected(self):
        self.assertRejects(make_zip([("../../etc/evil", b"x")]), "unsafe_path")

    def test_embedded_parent_segment_rejected(self):
        self.assertRejects(make_zip([("repo/../../evil.txt", b"x")]), "unsafe_path")

    def test_absolute_and_drive_paths_rejected(self):
        self.assertRejects(make_zip([("/etc/passwd", b"x")]), "unsafe_path")
        self.assertRejects(make_zip([("C:/Windows/evil.txt", b"x")]), "unsafe_path")

    def test_backslash_traversal_rejected(self):
        self.assertRejects(make_zip([("repo\\..\\evil.txt", b"x")]), "unsafe_path")

    def test_symlink_rejected(self):
        link_mode = stat.S_IFLNK | 0o777
        self.assertRejects(make_zip([("repo/link", b"/etc/passwd", link_mode)]), "symlink_in_archive")

    def test_invalid_archive(self):
        self.assertRejects(b"not a zip at all", "invalid_archive")

    def test_empty_archive(self):
        self.assertRejects(make_zip([]), "empty_repository")

    def test_compression_bomb_rejected(self):
        big = b"A" * (ingest.RATIO_MIN_SIZE * 3)
        self.assertRejects(make_zip([("repo/big.txt", big)]), "suspicious_compression")

    def test_too_many_entries(self):
        old = ingest.MAX_ENTRIES
        ingest.MAX_ENTRIES = 3
        try:
            self.assertRejects(make_zip([(f"r/f{i}.txt", b"x") for i in range(5)]), "too_many_entries")
        finally:
            ingest.MAX_ENTRIES = old

    def test_expansion_limit_enforced(self):
        old = ingest.MAX_EXTRACT_BYTES
        ingest.MAX_EXTRACT_BYTES = 1000
        try:
            self.assertRejects(make_zip([("r/a.txt", b"x" * 2000)]), "archive_too_large")
        finally:
            ingest.MAX_EXTRACT_BYTES = old

    def test_upload_size_limit(self):
        old = ingest.MAX_UPLOAD_BYTES
        ingest.MAX_UPLOAD_BYTES = 100
        try:
            self.assertRejects(make_zip([("r/a.txt", b"x" * 500)]), "archive_too_large")
        finally:
            ingest.MAX_UPLOAD_BYTES = old


class UrlTests(unittest.TestCase):
    def test_accepts_repository_roots(self):
        self.assertEqual(ingest.parse_github_url("github.com/acme/ledger-api"), ("acme", "ledger-api"))
        self.assertEqual(ingest.parse_github_url("https://github.com/acme/ledger-api.git/"), ("acme", "ledger-api"))

    def test_rejects_other_hosts_and_paths(self):
        for bad in ["https://evil.example/acme/x", "https://github.com/acme/x/tree/main", "ftp://github.com/a/b",
                    "", "github.com/", "github.com/../x", "http://localhost:8000/a/b"]:
            with self.subTest(url=bad), self.assertRaises(IngestError) as ctx:
                ingest.parse_github_url(bad)
            self.assertEqual(ctx.exception.code, "invalid_url")

    def test_branch_validation(self):
        self.assertEqual(ingest.validate_branch("release/2.x"), "release/2.x")
        self.assertEqual(ingest.validate_branch(""), "HEAD")
        for bad in ["../main", "main;rm -rf", "a" * 300, "/main"]:
            with self.subTest(branch=bad), self.assertRaises(IngestError):
                ingest.validate_branch(bad)

    def test_download_host_allowlist(self):
        ingest.check_allowed_url("https://codeload.github.com/acme/x/zip/refs/heads/main")
        for bad in ["http://github.com/a", "https://169.254.169.254/latest", "https://evil.example/x"]:
            with self.subTest(url=bad), self.assertRaises(IngestError) as ctx:
                ingest.check_allowed_url(bad)
            self.assertEqual(ctx.exception.code, "blocked_host")

    def test_download_uses_injected_opener_and_surfaces_not_found(self):
        import urllib.error

        class FakeOpener:
            def open(self, url, timeout=None):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with self.assertRaises(IngestError) as ctx:
            ingest.download_repository_archive("github.com/acme/none", "main", opener=FakeOpener())
        self.assertEqual(ctx.exception.code, "repository_not_found")

    def test_unreachable_upstream_reports_cleanly(self):
        import urllib.error

        class Down:
            def open(self, url, timeout=None):
                raise urllib.error.URLError("no route")

        with self.assertRaises(IngestError) as ctx:
            ingest.download_repository_archive("github.com/acme/x", "main", opener=Down())
        self.assertEqual(ctx.exception.code, "upstream_unavailable")


if __name__ == "__main__":
    unittest.main()
