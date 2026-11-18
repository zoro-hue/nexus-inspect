import unittest

from nexus import rules as R


def hits(rule_id, path, text):
    rule = R.RULES_BY_ID[rule_id]
    return rule.check(path, text.splitlines())


class SecretTests(unittest.TestCase):
    def test_flags_literal_key_and_redacts_value(self):
        h = hits("SEC-SECRET-001", "config/app.js", 'const apiKey = "sk_live_9f8e7d6c5b4a3210";')
        self.assertEqual([x.line for x in h], [1])
        self.assertNotIn("9f8e7d6c5b4a3210", h[0].evidence)
        self.assertIn("sk_", h[0].evidence)

    def test_ignores_placeholders_and_env_references(self):
        self.assertEqual(hits("SEC-SECRET-001", "a.py", 'API_KEY = "your-api-key-goes-here"'), [])
        self.assertEqual(hits("SEC-SECRET-001", "a.js", 'const apiKey = process.env.API_KEY;'), [])

    def test_env_file_secret(self):
        h = hits("SEC-SECRET-002", ".env", "STRIPE_SECRET=sk_live_abcdef1234567890")
        self.assertEqual(len(h), 1)
        self.assertNotIn("abcdef1234567890", h[0].evidence)

    def test_secret_rule_does_not_apply_to_env_files(self):
        rule = R.RULES_BY_ID["SEC-SECRET-001"]
        self.assertFalse(rule.applies(".env"))


class SqlTests(unittest.TestCase):
    def test_concatenation_flagged(self):
        self.assertEqual(len(hits("SEC-SQL-001", "x.js", 'const q = "SELECT * FROM t WHERE a = \'" + a + "\'";')), 1)

    def test_fstring_flagged(self):
        self.assertEqual(len(hits("SEC-SQL-001", "x.py", 'q = f"SELECT * FROM users WHERE id = {uid}"')), 1)

    def test_parameterized_not_flagged(self):
        self.assertEqual(hits("SEC-SQL-001", "x.js", 'db.query("SELECT * FROM t WHERE id = $1", [id])'), [])


class MiscSecurityTests(unittest.TestCase):
    def test_eval_literal_not_flagged(self):
        self.assertEqual(hits("SEC-EVAL-001", "a.js", 'eval("1+1")'), [])
        self.assertEqual(len(hits("SEC-EVAL-001", "a.js", "eval(userInput)")), 1)

    def test_method_named_exec_not_flagged(self):
        self.assertEqual(hits("SEC-EVAL-001", "a.py", "matcher.exec(text)"), [])

    def test_debug_only_in_prod_files(self):
        rule = R.RULES_BY_ID["SEC-DEBUG-001"]
        self.assertTrue(rule.applies("config/production.json"))
        self.assertFalse(rule.applies("config/dev.json"))
        self.assertEqual(len(hits("SEC-DEBUG-001", "config/production.json", '"debug": true')), 1)

    def test_authz_guarded_route_not_flagged(self):
        self.assertEqual(hits("SEC-AUTHZ-001", "r.js", 'router.get("/admin/x", requireRole("admin"), h)'), [])
        self.assertEqual(len(hits("SEC-AUTHZ-001", "r.js", 'router.get("/admin/x", h)')), 1)


class SlopAndGapTests(unittest.TestCase):
    def test_stub_with_no_operation_flagged(self):
        src = 'function pay(o) {\n  return { status: "succeeded" };\n}'
        self.assertEqual([h.line for h in hits("SLOP-STUB-001", "p.js", src)], [2])

    def test_success_after_real_call_not_flagged(self):
        src = 'function pay(o) {\n  const r = provider.charge(o);\n  return { status: "succeeded" };\n}'
        self.assertEqual(hits("SLOP-STUB-001", "p.js", src), [])

    def test_validation_present_suppresses_gap(self):
        src = 'const body = req.body;\nconst v = schema.parse(body);'
        self.assertEqual(hits("GAP-VALID-001", "r.js", src), [])

    def test_network_inside_try_not_flagged(self):
        src = 'try {\n  const r = await fetch(u);\n} catch (e) {\n  log(e);\n}'
        self.assertEqual(hits("GAP-ERR-001", "s.js", src), [])


class LooseEndTests(unittest.TestCase):
    def test_todo_in_comment_only(self):
        self.assertEqual(len(hits("LOOSE-TODO-001", "a.js", "// TODO: fix")), 1)
        self.assertEqual(hits("LOOSE-TODO-001", "a.js", 'const s = "TODO list";'), [])

    def test_dependency_usage_detection(self):
        files = {"package.json": '{"dependencies": {"express": "1", "lodash": "1", "@types/node": "1"}}',
                 "src/app.js": "const express = require('express');"}
        flagged = [(p, h.evidence) for p, h in R.check_unused_dependencies(files)]
        self.assertEqual(len(flagged), 1)
        self.assertIn("lodash", flagged[0][1])

    def test_no_sources_means_no_dependency_judgement(self):
        files = {"package.json": '{"dependencies": {"lodash": "1"}}'}
        self.assertEqual(R.check_unused_dependencies(files), [])

    def test_entry_points_never_dead(self):
        files = {"src/index.js": "x", "src/helpers/date.js": "y"}
        flagged = {p for p, _ in R.check_dead_modules(files)}
        self.assertEqual(flagged, {"src/helpers/date.js"})


class RegistryTests(unittest.TestCase):
    def test_every_rule_has_metadata_and_remediation(self):
        for r in R.RULES:
            self.assertIn(r.category, R.CATEGORIES, r.id)
            self.assertIn(r.severity, R.SEVERITIES, r.id)
            self.assertTrue(0 < r.confidence <= 1, r.id)
            self.assertTrue(r.remediation and r.impact and r.verification, r.id)
            self.assertTrue(r.check is not None or r.check_repo is not None, r.id)

    def test_rule_ids_unique(self):
        ids = [r.id for r in R.RULES]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()


class Phase5RuleScopeTests(unittest.TestCase):
    def test_test_paths_are_recognised(self):
        from nexus import rules as R
        self.assertTrue(R.is_test_path("tests/test_api.py"))
        self.assertTrue(R.is_test_path("src/__tests__/a.js"))
        self.assertTrue(R.is_test_path("pkg/widget.spec.ts"))
        self.assertFalse(R.is_test_path("src/service.py"))
        self.assertFalse(R.is_test_path("src/latest.py"))

    def test_stub_rule_does_not_apply_to_test_files(self):
        from nexus import rules as R
        rule = R.RULES_BY_ID["LOOSE-STUB-001"]
        self.assertTrue(rule.applies("src/service.py"))
        self.assertFalse(rule.applies("tests/test_service.py"))

    def test_namespace_package_import_resolves(self):
        from nexus import rules as R
        files = {"pkg/app.py": "from .plugins import helper\n", "pkg/plugins/helper.py": "x = 1\n"}
        self.assertEqual(R.check_broken_local_import(files), [])

    def test_missing_relative_module_is_still_reported(self):
        from nexus import rules as R
        files = {"pkg/app.py": "from .plugins import helper\n"}
        self.assertEqual(len(R.check_broken_local_import(files)), 1)

    def test_unimported_sample_and_docs_code_is_not_reported_as_dead(self):
        from nexus import rules as R
        files = {"docs/conf.py": "project = 'x'\n", "examples/demo/run.py": "print(1)\n", "src/orphan.py": "x = 1\n"}
        dead = {p for p, _ in R.check_dead_modules(files)}
        self.assertEqual(dead, {"src/orphan.py"})

    def test_placeholder_success_rule_skips_test_fixtures(self):
        from nexus import rules as R
        rule = R.RULES_BY_ID["SLOP-STUB-001"]
        self.assertTrue(rule.applies("src/api.py"))
        self.assertFalse(rule.applies("tests/test_api.py"))



class SampleAndTestScopeTests(unittest.TestCase):
    """Heuristic 'potential' rules apply to production code only; security rules apply everywhere."""

    def test_production_path_classification(self):
        self.assertTrue(R.is_production_path("src/app/service.py"))
        self.assertFalse(R.is_production_path("tests/test_api.py"))
        self.assertFalse(R.is_production_path("examples/demo/views.py"))
        self.assertFalse(R.is_production_path("docs/conf.py"))

    def test_root_level_test_js_is_a_test(self):
        self.assertTrue(R.is_test_path("test.js"))
        self.assertTrue(R.is_test_path("tests.py"))

    def test_test_file_is_not_reported_as_unimported_module(self):
        files = {"test.js": "describe('x', () => {});", "lib/util.js": "module.exports = 1;"}
        flagged = [p for p, _ in R.check_dead_modules(files)]
        self.assertNotIn("test.js", flagged)

    def test_validation_heuristic_not_applied_to_examples(self):
        rule = R.RULES_BY_ID["GAP-VALID-001"]
        self.assertFalse(rule.applies("examples/app/views.py"))
        self.assertTrue(rule.applies("src/app/views.py"))

    def test_error_handling_heuristic_not_applied_to_tests(self):
        rule = R.RULES_BY_ID["GAP-ERR-002"]
        self.assertFalse(rule.applies("tests/test_reqctx.py"))
        self.assertTrue(rule.applies("src/flask/config.py"))

    def test_eval_security_rule_still_applies_to_examples(self):
        rule = R.RULES_BY_ID["SEC-EVAL-001"]
        self.assertTrue(rule.applies("examples/js/app.js"))
