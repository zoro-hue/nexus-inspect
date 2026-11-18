import unittest

from nexus import ast_rules


def ids(source):
    return [(h.rule_id, h.line, h.end_line) for h in ast_rules.check_python(source)]


class PlaceholderFunctions(unittest.TestCase):
    def test_pass_body_is_a_stub(self):
        self.assertEqual(ids("def save():\n    pass\n"), [("LOOSE-STUB-001", 1, 2)])

    def test_ellipsis_body_is_a_stub(self):
        self.assertEqual(ids("def save():\n    ...\n"), [("LOOSE-STUB-001", 1, 2)])

    def test_not_implemented_is_a_stub(self):
        self.assertEqual(ids("def save():\n    raise NotImplementedError('todo')\n"), [("LOOSE-STUB-001", 1, 2)])

    def test_docstring_plus_pass_is_still_a_stub(self):
        self.assertEqual(ids('def save():\n    """Saves."""\n    pass\n'), [("LOOSE-STUB-001", 1, 3)])

    def test_real_body_is_not_a_stub(self):
        self.assertEqual(ids("def add(a, b):\n    return a + b\n"), [])

    def test_raise_of_another_exception_is_not_a_stub(self):
        self.assertEqual(ids("def f():\n    raise ValueError('bad')\n"), [])


class DeclarationsAreNotStubs(unittest.TestCase):
    def test_overload_signature_is_not_a_stub(self):
        src = "from typing import overload\n\n@overload\ndef f(x: int) -> int: ...\n"
        self.assertEqual(ids(src), [])

    def test_abstract_method_is_not_a_stub(self):
        src = "from abc import abstractmethod\n\n@abstractmethod\ndef f(self):\n    pass\n"
        self.assertEqual(ids(src), [])

    def test_protocol_member_is_not_a_stub(self):
        src = "from typing import Protocol\n\nclass P(Protocol):\n    def f(self) -> int: ...\n"
        self.assertEqual(ids(src), [])

    def test_abc_member_is_not_a_stub(self):
        src = "from abc import ABC\n\nclass B(ABC):\n    def f(self):\n        pass\n"
        self.assertEqual(ids(src), [])

    def test_type_checking_stub_is_not_a_stub(self):
        src = "import typing as t\n\nif t.TYPE_CHECKING:\n    def f(x): ...\n"
        self.assertEqual(ids(src), [])

    def test_raise_not_implemented_in_a_class_is_an_abstract_hook(self):
        src = "class Base:\n    def hook(self):\n        raise NotImplementedError\n"
        self.assertEqual(ids(src), [])

    def test_raise_not_implemented_at_module_level_is_still_a_stub(self):
        self.assertEqual(ids("def f():\n    raise NotImplementedError\n"), [("LOOSE-STUB-001", 1, 2)])

    def test_plain_class_method_with_pass_is_still_a_stub(self):
        src = "class Service:\n    def sync(self):\n        pass\n"
        self.assertEqual(ids(src), [("LOOSE-STUB-001", 2, 3)])


class IntentionalEmptyBodies(unittest.TestCase):
    def test_dunder_hook_with_pass_is_not_a_stub(self):
        src = "class Guard:\n    def __enter__(self):\n        pass\n"
        self.assertEqual(ids(src), [])

    def test_commented_handler_is_not_reported(self):
        src = "try:\n    x()\nexcept Exception:\n    # Keep the raw value if parsing fails.\n    pass\n"
        self.assertEqual(ids(src), [])


class SwallowedExceptions(unittest.TestCase):
    def test_bare_except_pass_is_reported(self):
        src = "try:\n    x()\nexcept:\n    pass\n"
        self.assertEqual(ids(src), [("GAP-ERR-002", 3, 4)])

    def test_except_exception_continue_is_reported(self):
        src = "for i in y:\n    try:\n        x()\n    except Exception:\n        continue\n"
        self.assertEqual(ids(src), [("GAP-ERR-002", 4, 5)])

    def test_handler_that_logs_is_not_reported(self):
        src = "try:\n    x()\nexcept Exception:\n    log.exception('x')\n"
        self.assertEqual(ids(src), [])

    def test_narrow_exception_pass_is_not_reported(self):
        src = "try:\n    x()\nexcept KeyError:\n    pass\n"
        self.assertEqual(ids(src), [])


class UnreachableCode(unittest.TestCase):
    def test_statement_after_return_is_reported(self):
        src = "def f():\n    return 1\n    print('never')\n"
        self.assertEqual(ids(src), [("LOOSE-DEAD-001", 3, 3)])

    def test_statement_after_raise_is_reported(self):
        src = "def f():\n    raise ValueError()\n    cleanup()\n"
        self.assertEqual(ids(src), [("LOOSE-DEAD-001", 3, 3)])

    def test_return_in_branch_is_not_reported(self):
        src = "def f(x):\n    if x:\n        return 1\n    return 2\n"
        self.assertEqual(ids(src), [])


class Robustness(unittest.TestCase):
    def test_unparseable_source_returns_no_hits(self):
        self.assertEqual(ast_rules.check_python("def broken(:\n"), [])

    def test_empty_source_returns_no_hits(self):
        self.assertEqual(ast_rules.check_python(""), [])

    def test_oversized_source_is_skipped(self):
        big = "x = 1\n" * (ast_rules.MAX_SOURCE_BYTES // 6 + 10)
        self.assertEqual(ast_rules.check_python("def f():\n    pass\n" + big), [])

    def test_hits_are_sorted_by_line(self):
        src = "def a():\n    pass\n\ndef b():\n    ...\n"
        lines = [h.line for h in ast_rules.check_python(src)]
        self.assertEqual(lines, sorted(lines))


if __name__ == "__main__":
    unittest.main()
