import unittest

from tests.corpus_runner import expected_set, load_cases, run_case


class CorpusTests(unittest.TestCase):
    def test_each_labelled_repository_matches_exactly(self):
        cases = list(load_cases())
        self.assertGreaterEqual(len(cases), 10)
        for name, root, spec in cases:
            with self.subTest(case=name):
                got, _ = run_case(root)
                self.assertEqual(got, expected_set(spec))

    def test_precision_and_recall_on_corpus(self):
        tp = fp = fn = 0
        for _, root, spec in load_cases():
            got, _ = run_case(root)
            exp = expected_set(spec)
            tp += len(got & exp); fp += len(got - exp); fn += len(exp - got)
        self.assertEqual((fp, fn), (0, 0))

    def test_no_secret_value_leaks_into_findings(self):
        _, root, _ = next(c for c in load_cases() if c[0] == "secret_hardcoded")
        _, result = run_case(root)
        for f in result.findings:
            self.assertNotIn("9f8e7d6c5b4a3210", f["evidence"])

    def test_findings_are_reproducible_and_unique(self):
        for _, root, _ in load_cases():
            a = run_case(root)[1].findings
            b = run_case(root)[1].findings
            self.assertEqual([f["id"] for f in a], [f["id"] for f in b])
            self.assertEqual(len({f["id"] for f in a}), len(a))


if __name__ == "__main__":
    unittest.main()
