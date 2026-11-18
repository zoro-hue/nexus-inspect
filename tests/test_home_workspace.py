"""Static regression checks for the homepage Repository Investigation Workspace.

These read the shipped files, so they run without a browser and cover the
parts that must not regress: the old self-drawing graph is gone, the sample is
labelled as illustrative, the default state is visible without JavaScript, and
the page keeps no inline script (the CSP is script-src 'self').
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def investigation_section(html):
    start = html.index('id="investigation"')
    end = html.index("<!-- Section 7", start)
    return html[start:end]


class HomeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.html = read("index.html")
        self.section = investigation_section(self.html)
        self.css = read("home.css")
        self.js = read("home.js")

    def test_old_graph_markup_and_styles_are_removed(self):
        self.assertNotIn("inv-graph", self.html)
        self.assertNotIn("inv-graph", self.css)
        self.assertNotIn("graphContainer", self.html)

    def test_workspace_has_three_panels_and_is_labelled_illustrative(self):
        self.assertIn('id="invWorkspace"', self.section)
        self.assertIn("inv-tree-pane", self.section)
        self.assertIn("inv-code-pane", self.section)
        self.assertIn("inv-rel-pane", self.section)
        self.assertIn("Illustrative sample", self.section)
        self.assertIn("not produced by a scan", self.section)

    def test_default_state_is_visible_without_javascript(self):
        # Exactly one code file and one finding are shown by default; the rest are hidden.
        code_blocks = re.findall(r'<pre class="inv-code" data-file="[^"]+"( hidden)?>', self.section)
        self.assertEqual(len(code_blocks), 6)
        self.assertEqual(sum(1 for h in code_blocks if not h), 1)
        findings = re.findall(r'<article class="inv-finding" data-finding="[^"]+"( data-file="[^"]+")?[^>]*?( hidden)?>', self.section)
        self.assertEqual(len(findings), 3)
        self.assertEqual(self.section.count('class="inv-finding" data-finding="sec-sql" data-file="src/db/users.js" data-line="5">'), 1)

    def test_section_has_no_inline_script(self):
        self.assertNotRegex(self.section, r"<script\b")
        self.assertNotRegex(self.section, r"\son[a-z]+=")

    def test_script_is_wired_and_cleaned_up(self):
        self.assertIn("function initInvestigation()", self.js)
        self.assertIn("initInvestigation();", self.js)
        self.assertIn("cleanupInvestigation();", self.js)
        self.assertIn("removeEventListener('click', onInvClick)", self.js)

    def test_reduced_motion_disables_workspace_animation(self):
        self.assertRegex(self.css, r"@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.inv-finding:not\(\[hidden\]\)\s*\{\s*animation:\s*none")

    def test_assets_are_cache_busted_together(self):
        self.assertIn("home.css?v=10", self.html)
        self.assertIn("home.js?v=9", self.html)


if __name__ == "__main__":
    unittest.main()
