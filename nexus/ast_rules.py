"""Python AST checks that complement the line-based rules.

These run on Python source only. They parse the file with the standard library `ast` module, which never
executes repository code. A file that does not parse produces no AST findings; the scanner records it as
malformed elsewhere. Every check reports the exact line range it observed, so a reader can verify it.
"""
import ast
from dataclasses import dataclass
from typing import List, Optional

MAX_SOURCE_BYTES = 512 * 1024  # larger files are skipped rather than parsed


@dataclass(frozen=True)
class AstHit:
    rule_id: str
    line: int
    end_line: int
    explanation: str
    status: str  # "confirmed" when the code shape is unambiguous, "potential" when intent could differ


PLACEHOLDER_BODY_STATUS = "confirmed"


# Decorators and base classes that mark a function as a declaration rather than an unfinished implementation.
DECLARATION_DECORATORS = {"overload", "abstractmethod", "abstractproperty"}
DECLARATION_BASES = {"Protocol", "ABC"}


def _names(nodes):
    out = set()
    for n in nodes:
        target = n.value if isinstance(n, ast.Subscript) else n
        if isinstance(target, ast.Call):
            target = target.func
        if isinstance(target, ast.Name):
            out.add(target.id)
        elif isinstance(target, ast.Attribute):
            out.add(target.attr)
    return out


def _is_type_checking_block(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and bool(_names([node.test]) & {"TYPE_CHECKING"})


def _declaration_context(fn, parents):
    """Why a stub-shaped function is a declaration: a type-checking stub, an abstract or protocol member, or a
    decorator that declares an overload. Returns a reason string, or None for a real candidate."""
    if DECLARATION_DECORATORS & _names(fn.decorator_list):
        return "declaration decorator"
    cur = parents.get(fn)
    while cur is not None:
        if _is_type_checking_block(cur):
            return "type-checking stub"
        if isinstance(cur, ast.ClassDef):
            metaclass = {k.arg: k.value for k in cur.keywords}.get("metaclass")
            if DECLARATION_BASES & _names(cur.bases) or (metaclass is not None and "ABCMeta" in _names([metaclass])):
                return "protocol or abstract class member"
        cur = parents.get(cur)
    return None


def _inside_class(fn, parents):
    cur = parents.get(fn)
    while cur is not None:
        if isinstance(cur, ast.ClassDef):
            return True
        cur = parents.get(cur)
    return False


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _placeholder_body(fn) -> Optional[str]:
    """A function whose only real statement is `pass`, `...`, or `raise NotImplementedError`."""
    body = [n for n in fn.body if not _is_docstring(n)]
    if not body:
        return None
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return "pass"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return "..."
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        target = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        if isinstance(target, ast.Name) and target.id == "NotImplementedError":
            return "raise NotImplementedError"
    return None


def _has_comment_inside(lines, handler):
    """A comment inside the handler states the author's intent, so the handler is not reported as unexplained."""
    first = handler.lineno
    last = getattr(handler, "end_lineno", handler.lineno)
    return any(lines[i - 1].lstrip().startswith("#") for i in range(first, last + 1) if 0 < i <= len(lines))


def _swallowed_exception(handler: ast.ExceptHandler) -> Optional[str]:
    """A bare `except` or `except Exception` whose body only passes or continues, hiding the failure."""
    if handler.type is None:
        kind = "bare except"
    elif isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException"):
        kind = f"except {handler.type.id}"
    else:
        return None
    body = [n for n in handler.body if not _is_docstring(n)]
    if body and all(isinstance(n, (ast.Pass, ast.Continue)) for n in body):
        return kind
    return None


def _unreachable_after_return(block: List[ast.stmt]) -> Optional[ast.stmt]:
    for i, stmt in enumerate(block[:-1]):
        if isinstance(stmt, (ast.Return, ast.Raise)):
            following = block[i + 1]
            if not _is_docstring(following):
                return following
    return None


def parses(source: str) -> bool:
    """True when the source is valid Python (or too large to check, which is reported separately)."""
    if len(source.encode("utf-8", errors="ignore")) > MAX_SOURCE_BYTES:
        return True
    try:
        ast.parse(source)
        return True
    except (SyntaxError, ValueError):
        return False


def check_python(source: str) -> List[AstHit]:
    """Return AST-level hits for one Python file, or [] when the source cannot be parsed or is too large."""
    if len(source.encode("utf-8", errors="ignore")) > MAX_SOURCE_BYTES:
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    source_lines = source.splitlines()
    hits: List[AstHit] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = _placeholder_body(node)
            # `raise NotImplementedError` inside a class is the usual shape of an abstract hook, not an unfinished feature.
            if kind == "raise NotImplementedError" and _inside_class(node, parents):
                kind = None
            # Dunder hooks such as __enter__ or __exit__ are often intentionally empty.
            if node.name.startswith("__") and node.name.endswith("__"):
                kind = None
            if kind is not None and _declaration_context(node, parents) is None:
                end = getattr(node, "end_lineno", node.lineno)
                hits.append(AstHit(
                    "LOOSE-STUB-001", node.lineno, end,
                    f"Function '{node.name}' has no implementation: its only statement is {kind}.",
                    PLACEHOLDER_BODY_STATUS,
                ))
        if isinstance(node, ast.ExceptHandler):
            kind = _swallowed_exception(node)
            if kind is not None and not _has_comment_inside(source_lines, node):
                end = getattr(node, "end_lineno", node.lineno)
                hits.append(AstHit(
                    "GAP-ERR-002", node.lineno, end,
                    f"A {kind} handler discards the error without logging, re-raising, or recording it.",
                    "confirmed",
                ))
        for field_name in ("body", "orelse", "finalbody"):
            block = getattr(node, field_name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                dead = _unreachable_after_return(block)
                if dead is not None:
                    end = getattr(dead, "end_lineno", dead.lineno)
                    hits.append(AstHit(
                        "LOOSE-DEAD-001", dead.lineno, end,
                        "This statement follows a return or raise in the same block, so it can never run.",
                        "confirmed",
                    ))
    hits.sort(key=lambda h: (h.line, h.rule_id))
    return hits
