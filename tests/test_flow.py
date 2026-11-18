"""Unit tests for the single-file data-flow helpers (nexus/flow.py) and their rule wiring."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import flow  # noqa: E402
from nexus import rules as R  # noqa: E402


class TaintTracking(unittest.TestCase):
    def test_source_line_is_reported_through_reassignment(self):
        lines = ['fmt = request.args["f"]', 'target = "r." + fmt', 'os.system("c " + target)']
        hits = flow.command_flow_hits(".py", lines)
        self.assertEqual(hits, [(3, "Request input reaches this command from line 1 without a sanitizer.")])

    def test_sanitizer_clears_taint(self):
        lines = ['name = os.path.basename(request.args["n"])', 'os.system("c " + name)']
        self.assertEqual(flow.command_flow_hits(".py", lines), [])

    def test_literal_reassignment_clears_taint(self):
        lines = ['fmt = request.args["f"]', 'fmt = "pdf"', 'os.system("c " + fmt)']
        self.assertEqual(flow.command_flow_hits(".py", lines), [])

    def test_direct_use_on_same_line_is_reported_on_that_line(self):
        self.assertEqual(flow.ssrf_flow_hits(".py", ['requests.get(request.args["u"])']),
                         [(1, "The URL of this outbound request comes from request input on this line.")])

    def test_constant_url_is_not_reported(self):
        self.assertEqual(flow.ssrf_flow_hits(".js", ['const r = await axios.get("https://api.example.com/x");']), [])

    def test_javascript_path_flow_through_path_join(self):
        lines = ["const name = req.query.file;", "const full = path.join(BASE, name);", "fs.readFile(full, cb);"]
        self.assertEqual(flow.path_flow_hits(".js", lines)[0][0], 3)

    def test_go_request_value_into_sh_c(self):
        lines = ['name := r.URL.Query().Get("n")', 'exec.Command("sh", "-c", "tar "+name)']
        hits = flow.command_flow_hits(".go", lines)
        self.assertEqual(hits[0][0], 2)

    def test_php_superglobal_in_system(self):
        self.assertEqual(flow.command_flow_hits(".php", ['system("ls " . $_GET["d"]);'])[0][0], 1)

    def test_unknown_extension_has_no_sinks(self):
        self.assertEqual(flow.command_flow_hits(".rb", ['system(request.args["x"])']), [])

    def test_rules_expose_flow_hits_with_correct_rule_ids(self):
        by_id = {r.id: r for r in R.RULES}
        lines = ['fmt = request.args["f"]', 'os.system("c " + fmt)']
        self.assertTrue(by_id["SEC-CMD-001"].check("svc/a.py", lines))
        self.assertTrue(by_id["SEC-SSRF-001"].check("svc/a.py", ['u = request.args["u"]', 'requests.get(u)']))


class ExemptionsAndCalibration(unittest.TestCase):
    def test_runnable_script_is_exempt_from_dead_module_rule(self):
        files = {"tools/cli.py": "#!/usr/bin/env python3\nprint(1)\n", "lib/core.py": "def f():\n    return 1\n"}
        flagged = [p for p, _ in R.check_dead_modules(files)]
        self.assertNotIn("tools/cli.py", flagged)

    def test_main_guard_script_is_exempt(self):
        files = {"scripts/job.py": 'if __name__ == "__main__":\n    main()\n', "lib/core.py": "x = 1\n"}
        self.assertNotIn("scripts/job.py", [p for p, _ in R.check_dead_modules(files)])

    def test_new_rules_are_potential_and_calibrated_low_or_medium(self):
        by_id = {r.id: r for r in R.RULES}
        for rid in ("SEC-SSRF-001", "SEC-CRYPTO-001", "SEC-DESER-001", "SEC-CMD-001", "SEC-XSS-001", "SEC-PATH-001"):
            self.assertEqual(by_id[rid].evidence_class, "potential", rid)
        self.assertEqual(by_id["SEC-CRYPTO-001"].severity, "low")
        self.assertEqual(by_id["SEC-DESER-001"].severity, "medium")


if __name__ == "__main__":
    unittest.main()
