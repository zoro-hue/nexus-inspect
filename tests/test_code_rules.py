"""Regression tests for the command-injection, XSS, unsafe-deserialization and path-traversal rules.

Each case is a single-line snippet with the expected outcome. The vulnerable forms and their safe
counterparts are both covered, so a rule that starts flagging safe code fails here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import benchmark  # noqa: E402
from nexus import rules as R  # noqa: E402

NEW_IDS = ("SEC-CMD-001", "SEC-XSS-001", "SEC-DESER-001", "SEC-PATH-001")
BY_ID = {r.id: r for r in R.RULES}

# (path, code, expected rule id or None)
CASES = [
    ("svc/a.py", "subprocess.call(cmd, shell=True)", "SEC-CMD-001"),
    ("svc/a.py", 'subprocess.run(f"convert {name} out.png", shell=True)', "SEC-CMD-001"),
    ("svc/a.py", 'os.popen("ls " + name)', "SEC-CMD-001"),
    ("svc/a.py", 'subprocess.run(["ls", user_dir])', None),
    ("svc/a.py", 'subprocess.run("git status", shell=True)', None),
    ("svc/a.py", 'os.system("reboot")', None),
    ("src/a.js", 'require("child_process").exec(req.query.cmd)', "SEC-CMD-001"),
    ("src/a.js", "const { exec } = require('child_process'); exec(`tar ${file}`)", "SEC-CMD-001"),
    ("src/a.js", 'exec("git status")', None),
    ("src/a.js", "const m = /x/.exec(text);", None),
    ("web/a.js", "container.innerHTML = template;", "SEC-XSS-001"),
    ("web/a.jsx", "<div dangerouslySetInnerHTML={{ __html: post.body }} />", "SEC-XSS-001"),
    ("web/a.js", 'container.innerHTML = "";', None),
    ("web/a.js", "node.textContent = message;", None),
    ("svc/a.py", "data = marshal.loads(blob)", "SEC-DESER-001"),
    ("svc/a.py", "cfg = yaml.load(f, Loader=yaml.FullLoader)", "SEC-DESER-001"),
    ("svc/a.py", "cfg = yaml.load(text)", "SEC-DESER-001"),
    ("svc/a.py", "cfg = yaml.load(text, Loader=yaml.SafeLoader)", None),
    ("svc/a.py", "cfg = yaml.safe_load(text)", None),
    ("svc/a.py", 'open(request.args["file"])', "SEC-PATH-001"),
    ("svc/a.py", 'path = base / request.args["f"]', "SEC-PATH-001"),
    ("svc/a.py", 'name = os.path.basename(request.args["name"])', None),
    ("svc/a.py", "with open(os.path.join(BASE_DIR, name)) as fh:", None),
    ("src/a.js", "res.sendFile(req.params.name)", "SEC-PATH-001"),
    ("src/a.js", "fs.readFile(path.join(DIR, file), cb)", None),
]


class CodeRuleCases(unittest.TestCase):
    def test_each_case_matches_expected_rule(self):
        for path, code, want in CASES:
            with self.subTest(code=code):
                got = {rid for rid in NEW_IDS
                       if BY_ID[rid].applies(path) and BY_ID[rid].check(path, code.split("\n"))}
                self.assertEqual(got, {want} if want else set())

    def test_rules_registered_with_evidence_and_remediation(self):
        for rid in NEW_IDS:
            self.assertIn(rid, BY_ID)
            rule = BY_ID[rid]
            self.assertEqual(rule.category, "security")
            self.assertIn(rule.evidence_class, ("confirmed", "potential", "informational"))
            self.assertTrue(rule.remediation and rule.verification)

    def test_js_exec_name_is_not_treated_as_eval(self):
        eval_rule = BY_ID["SEC-EVAL-001"]
        self.assertFalse(eval_rule.check("src/a.js", ["exec(userInput)"]))
        self.assertTrue(eval_rule.check("src/a.js", ["eval(userInput)"]))
        self.assertTrue(eval_rule.check("src/a.py", ["exec(user_code)"]))


class HeldoutLabelledScope(unittest.TestCase):
    """Each held-out case must match exactly on the rule ids it labels (both held-out sets)."""

    def test_heldout_set_one_matches_labels_on_labelled_rules(self):
        self._check_set("heldout", 6)

    def test_heldout_set_two_matches_labels_on_labelled_rules(self):
        self._check_set("heldout2", 10)

    def _check_set(self, folder, minimum):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", folder)
        seen = 0
        for name in sorted(os.listdir(root)):
            spec_path = os.path.join(root, name, "expected.json")
            if not os.path.isfile(spec_path):
                continue
            import json
            with open(spec_path, encoding="utf-8") as fh:
                spec = json.load(fh)
            labelled = {e["ruleId"] for e in spec["expected"]}
            predicted, _, _ = benchmark.run_case(os.path.join(root, name))
            predicted = {k for k in predicted if k[0] in labelled}
            expected = {benchmark._key(e) for e in spec["expected"]}
            with self.subTest(case=name):
                self.assertEqual(predicted, expected)
            seen += 1
        self.assertGreaterEqual(seen, minimum)


if __name__ == "__main__":
    unittest.main()
