"""Deterministic detection rules.

Each rule is a small object with metadata (id, category, severity, confidence, remediation,
verification) and a check. File rules receive one file at a time; repo rules receive every
text file so they can correlate evidence across the repository (for example, a declared
dependency that no source file imports). Rules report only what the text shows; they never
claim authorship and never execute repository code.
"""
import json
import os
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

from . import flow as _flow

SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = ("security", "slop", "gap", "loose")

SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".rb", ".php"}
CONFIG_EXTS = {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".properties", ".env", ".sh"}
TEXT_EXTS = SOURCE_EXTS | CONFIG_EXTS

PLACEHOLDER_RE = re.compile(r"(?i)(example|placeholder|changeme|your[_-]|dummy|xxxx|<|\$\{|process\.env|os\.environ|getenv)")


def redact(value: str) -> str:
    """Show only enough of a secret to locate it; never the full value."""
    return f"{value[:3]}{'*' * 8} (length {len(value)})"


# Token shapes issued by common providers. They are matched on their own, so a token is redacted even when it is not
# assigned to a credential-named variable. Lengths are floors chosen to avoid matching ordinary identifiers.
TOKEN_SHAPES = (
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("Anthropic or OpenAI key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}")),
)
PEM_BEGIN_RE = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
PEM_END_RE = re.compile(r"-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
PEM_MAX_LINES = 200
PEM_REDACTED = "[private key material redacted]"


def redact_line(line: str) -> str:
    """Redact every secret-looking value in a source line before it is stored or shown."""
    out = line
    for pattern in (SECRET_RE, ENV_SECRET_RE):
        for m in list(pattern.finditer(out)):
            value = m.group(m.lastindex)
            out = out.replace(value, redact(value))
    for _name, pattern in TOKEN_SHAPES:
        out = pattern.sub(lambda m: redact(m.group(0)), out)
    return out


def pem_body_lines(lines):
    """1-based line numbers inside a PEM private key block, including its BEGIN and END lines.
    Key material is base64 and cannot be recognised line by line, so every line of the block is treated as secret."""
    out, inside, start = set(), False, 0
    for i, line in enumerate(lines, 1):
        if not inside and PEM_BEGIN_RE.search(line):
            inside, start = True, i
        elif inside and PEM_END_RE.search(line):
            out.update(range(start, i + 1))
            inside = False
        elif inside and i - start > PEM_MAX_LINES:
            out.update(range(start, i + 1))
            inside = False
    return out


@dataclass(frozen=True)
class Hit:
    line: int          # 1-based line number
    evidence: str      # the offending source line, secrets already redacted
    explanation: str = ""
    end_line: Optional[int] = None


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    title: str
    severity: str
    confidence: float
    impact: str
    remediation: str
    verification: Tuple[str, ...]
    applies: Callable[[str], bool]
    check: Optional[Callable[[str, List[str]], List[Hit]]] = None
    # Repo rules: (files: {path: lines}) -> [(path, Hit)]
    check_repo: Optional[Callable[[dict], List[Tuple[str, Hit]]]] = None
    method: str = "pattern"
    # How strong the evidence is for one finding: "confirmed" when the code shape is unambiguous,
    # "potential" when the rule is a heuristic that a reviewer must check, "informational" for markers.
    evidence_class: str = "potential"


def ext(path: str) -> str:
    base = os.path.basename(path)
    if base.startswith(".env"):
        return ".env"
    return os.path.splitext(path)[1].lower()


def is_source(path: str) -> bool:
    return ext(path) in SOURCE_EXTS


def is_text_file(path: str) -> bool:
    return ext(path) in TEXT_EXTS


def is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith(("//", "#", "/*", "*", "--", "<!--"))


# ---------- SECURITY ----------

SECRET_RE = re.compile(
    r"""(?i)\b(api[_-]?key|apikey|client[_-]?secret|secret|access[_-]?token|auth[_-]?token|password|passwd)\b["']?\s*[:=]\s*["']([^"'\s]{12,})["']"""
)


def check_secret(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        m = SECRET_RE.search(line)
        if m and not PLACEHOLDER_RE.search(m.group(2)):
            value = m.group(2)
            ev = line.strip().replace(value, redact(value))
            hits.append(Hit(i, ev, f"A literal value is assigned to `{m.group(1)}` in source or configuration."))
    return hits


ENV_SECRET_RE = re.compile(r"^\s*([A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)\s*=\s*[\"']?([^\s\"'#]{12,})")


def check_env_secret(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        m = ENV_SECRET_RE.search(line)
        if m and not PLACEHOLDER_RE.search(m.group(3)):
            ev = line.strip().replace(m.group(3), redact(m.group(3)))
            hits.append(Hit(i, ev, f"`{m.group(1)}` holds a real-looking value in a committed env file."))
    return hits


def check_private_key(path, lines):
    """Report the block header only. The key material is never copied into evidence."""
    hits = []
    for i, line in enumerate(lines, 1):
        if PEM_BEGIN_RE.search(line):
            hits.append(Hit(i, line.strip(), "A private key block is stored in this file. The key material is not shown."))
    return hits


def check_token_shapes(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        for name, pattern in TOKEN_SHAPES:
            for m in pattern.finditer(line):
                if PLACEHOLDER_RE.search(m.group(0)):
                    continue
                hits.append(Hit(i, redact_line(line.strip()),
                                f"A value shaped like a {name} appears in this file. The value is redacted."))
                break
    return hits


SQL_CONCAT_RE = re.compile(
    r"""(?i)\b(select|insert|update|delete)\b[^\n]*["'`]\s*\+|\+\s*["'`][^\n]*\b(select|insert|update|delete)\b"""
)
SQL_FSTRING_RE = re.compile(r"""(?i)\bf["'][^"']*\b(select|insert|update|delete)\b[^"']*\{""")
SQL_TEMPLATE_RE = re.compile(r"(?i)`[^`]*\b(select|insert|update|delete)\b[^`]*\$\{")
SQL_CLAUSE_RE = re.compile(r"(?i)\b(from|into|where|set|values|table)\b")


def check_sql(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        if not SQL_CLAUSE_RE.search(line):
            continue
        if SQL_CONCAT_RE.search(line) or SQL_FSTRING_RE.search(line) or SQL_TEMPLATE_RE.search(line):
            hits.append(Hit(i, line.strip(), "A SQL statement is built from strings that may contain untrusted input."))
    return hits


EVAL_RE = re.compile(r"""(?<![\w.])(eval|exec)\s*\(\s*(?!["'])""")


JS_EVAL_RE = re.compile(r"""(?<![\w.])(eval)\s*\(\s*(?!["'])""")


def check_eval(path, lines):
    rx = EVAL_RE if ext(path) == ".py" else JS_EVAL_RE
    return [Hit(i, l.strip(), f"`{rx.search(l).group(1)}` runs a non-literal expression.")
            for i, l in enumerate(lines, 1) if rx.search(l)]


# ---- Command injection, XSS, unsafe deserialization and path traversal (single-line evidence) ----
# Each check reports only a line whose dangerous sink receives non-literal input from the same line.
# Multi-line data flow is not traced; that limit is documented in the rule's verification text.

_PY_SUBPROCESS_RE = re.compile(r"\bsubprocess\.(run|call|Popen|check_output|check_call|getoutput|getstatusoutput)\s*\(")
_PY_OS_SHELL_RE = re.compile(r"\bos\.(system|popen)\s*\(")
_JS_EXEC_RE = re.compile(r"(?<![\w.])(exec|execSync)\s*\(|\bchild_process\b[^;]*\.(exec|execSync)\s*\(")
_PY_LITERAL_ARG_RE = re.compile(r"""\(\s*[fF]?(["'])(?:(?!\1).)*\1\s*[,)]""")
_JS_LITERAL_ARG_RE = re.compile(r"""\(\s*(["'])(?:(?!\1).)*\1\s*[,)]""")


def check_command_injection(path, lines):
    hits = []
    py = ext(path) == ".py"
    for i, line in enumerate(lines, 1):
        if py:
            m = _PY_SUBPROCESS_RE.search(line)
            if m and re.search(r"\bshell\s*=\s*True\b", line):
                lit = _PY_LITERAL_ARG_RE.search(line[m.start():])
                interpolated = lit is not None and re.search(r"""[fF]["'][^"']*\{""", lit.group(0))
                if lit is None or interpolated:
                    hits.append(Hit(i, line.strip(), "A shell command is built from a non-literal value with shell=True."))
                continue
            m = _PY_OS_SHELL_RE.search(line)
            if m and not re.search(r"""\(\s*[fF]?["'][^"'{]*["']\s*\)""", line[m.start():]):
                hits.append(Hit(i, line.strip(), f"`os.{m.group(1)}` runs a command built from a non-literal value."))
        else:
            m = _JS_EXEC_RE.search(line)
            if m and not _JS_LITERAL_ARG_RE.search(line[m.start():]):
                hits.append(Hit(i, line.strip(), "`exec` runs a shell command built from a non-literal value. Prefer execFile with an argument array."))
    return _merge_flow_hits(hits, _flow.command_flow_hits(ext(path), lines), lines)


def _merge_flow_hits(hits, flow_hits, lines):
    """Add flow-based hits for lines not already reported; keep the direct hit when both exist."""
    seen = {h.line for h in hits}
    for line_no, explanation in flow_hits:
        if line_no not in seen:
            hits.append(Hit(line_no, lines[line_no - 1].strip(), explanation))
            seen.add(line_no)
    hits.sort(key=lambda h: h.line)
    return hits


_JS_HTML_SINK_RE = re.compile(r"\.(innerHTML|outerHTML)\s*\+?=(?!=)\s*(.*)$|dangerouslySetInnerHTML\s*=\s*\{\{\s*__html\s*:\s*(.*)")
_JS_LITERAL_RHS_RE = re.compile(r"""^\s*(["'])[^"'`]*\1\s*[;,}]?\s*$""")


def check_xss_sink(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        m = _JS_HTML_SINK_RE.search(line)
        if not m:
            continue
        rhs = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        if _JS_LITERAL_RHS_RE.match(rhs) or rhs.strip() in ("''", '""', "``"):
            continue
        hits.append(Hit(i, line.strip(), "HTML is assigned from a non-literal value. Untrusted input here runs as markup."))
    return hits


_PY_DESER_RE = re.compile(r"\b(pickle|cPickle|marshal|dill)\.(loads?|Unpickler)\s*\(")
_PY_YAML_UNSAFE_RE = re.compile(r"\byaml\.(load|unsafe_load|full_load)\s*\(")
_PY_SAFE_LOADER_RE = re.compile(r"Loader\s*=\s*(yaml\.)?(Safe|CSafe)Loader\b")


def check_unsafe_deserialization(path, lines):
    if ext(path) != ".py":
        return []
    hits = []
    for i, line in enumerate(lines, 1):
        m = _PY_DESER_RE.search(line)
        if m:
            hits.append(Hit(i, line.strip(), f"`{m.group(1)}.{m.group(2)}` can run code from attacker-supplied bytes."))
            continue
        m = _PY_YAML_UNSAFE_RE.search(line)
        if m and (m.group(1) != "load" or not _PY_SAFE_LOADER_RE.search(line)):
            hits.append(Hit(i, line.strip(), f"`yaml.{m.group(1)}` without a safe loader can construct arbitrary Python objects."))
    return hits


_REQUEST_SOURCE_RE = re.compile(r"\b(request|req)\.(args|GET|POST|query|params|body|form|files|json|data|values|cookies)\b")
_FILE_SINK_RE = re.compile(r"\b(open|send_file|send_from_directory|path\.join|readFile|readFileSync|createReadStream|sendFile|download)\s*\(")
_PATH_OPERATOR_RE = re.compile(r"/\s*(request|req)\.")  # pathlib join: base / request.args[...]
_PATH_SANITIZER_RE = re.compile(r"\b(basename|secure_filename|realpath|normpath|resolve|commonpath)\b")


def check_path_traversal(path, lines):
    hits = []
    for i, line in enumerate(lines, 1):
        sink = _FILE_SINK_RE.search(line) or _PATH_OPERATOR_RE.search(line)
        if _REQUEST_SOURCE_RE.search(line) and sink and not _PATH_SANITIZER_RE.search(line):
            hits.append(Hit(i, line.strip(), "A file path is built from request input without a basename or containment check."))
    return _merge_flow_hits(hits, _flow.path_flow_hits(ext(path), lines), lines)


def check_ssrf(path, lines):
    return _merge_flow_hits([], _flow.ssrf_flow_hits(ext(path), lines), lines)


# Weak primitives. A digest can be a checksum, so these are potential findings for a reviewer, not confirmed defects.
_WEAK_CRYPTO_RE = re.compile(
    r"\bhashlib\.(md5|sha1)\(|\bAES\.new\(.*MODE_ECB|\bDES\.new\(|\bARC4\.new\("
    r"|createHash\(\s*[\"'](md5|sha1)[\"']|createCipheriv\(\s*[\"'](des|des-ede3|rc4|aes-\d+-ecb)"
    r"|\bmd5\.(New|Sum)\(|\bsha1\.(New|Sum)\(|\bdes\.NewCipher\("
    r"|MessageDigest\.getInstance\(\s*\"(MD5|SHA-?1)\"|Cipher\.getInstance\(\s*\"(DES|RC4|AES/ECB)")
_PHP_WEAK_RE = re.compile(r"(?<![\w.])(md5|sha1)\s*\(")  # bare md5()/sha1() calls are PHP's digest functions


def check_weak_crypto(path, lines):
    php = ext(path) == ".php"
    out = []
    for i, line in enumerate(lines, 1):
        if is_comment(line):
            continue
        if _WEAK_CRYPTO_RE.search(line) or (php and _PHP_WEAK_RE.search(line)):
            out.append(Hit(i, line.strip(), "A weak hash or cipher is used. Confirm its purpose: a checksum or cache key is fine, a password digest or an encryption mode needs replacing."))
    return out


DEBUG_RE = re.compile(r"""(?i)\bdebug\b["']?\s*[:=]\s*(true|1|"true")""")


def check_debug_prod(path, lines):
    return [Hit(i, l.strip(), "Debug mode is switched on in a file that looks like production configuration.")
            for i, l in enumerate(lines, 1) if DEBUG_RE.search(l)]


def applies_prod_config(path):
    base = os.path.basename(path).lower()
    return "prod" in base and ext(path) in CONFIG_EXTS | {".py", ".js", ".ts"}


AUTHZ_ROUTE_RE = re.compile(r"""(router|app)\.(get|post|put|patch|delete)\(\s*["'`][^"'`]*/(admin|internal)\b""")
AUTHZ_GUARD_RE = re.compile(r"(?i)(requireRole|requireAuth|authorize|authenticate|isAdmin|ensureAuth|verifyToken|login_required|permission_required|@auth)")


def check_missing_authz(path, lines):
    return [Hit(i, l.strip(), "A sensitive route is registered without a visible authorization guard on the same line.")
            for i, l in enumerate(lines, 1) if AUTHZ_ROUTE_RE.search(l) and not AUTHZ_GUARD_RE.search(l)]



# ---------- AI SLOP / STUBS ----------

STUB_RETURN_RE = re.compile(
    r"""(?i)\breturn\s*\(?\s*\{?\s*["']?(status|success|ok|paid|result)["']?\s*[:=]\s*["']?(succeeded|success|ok|paid|true)["']?"""
)
FUNC_HEADER_RE = re.compile(r"(\bdef\s+\w+|\bfunction\b|=>|\)\s*\{\s*$)")
CALL_RE = re.compile(r"\w+\s*\(")


def check_hardcoded_success(path, lines):
    hits = []
    for i, line in enumerate(lines):
        if not STUB_RETURN_RE.search(line):
            continue
        # Find the enclosing function header within a few lines.
        header = None
        for j in range(i - 1, max(-1, i - 6), -1):
            if FUNC_HEADER_RE.search(lines[j]):
                header = j
                break
        if header is None:
            continue
        body = [b for b in lines[header + 1:i] if b.strip() and not is_comment(b)]
        if len(body) <= 1 and not any(CALL_RE.search(b) for b in body):
            hits.append(Hit(i + 1, line.strip(),
                            "The function returns a success status without performing the operation it promises."))
    return hits


# ---------- CODING GAPS ----------

REQ_INPUT_RE = re.compile(r"(req\.body|request\.(json|get_json|form|data)\b|request\.body)")
VALIDATION_RE = re.compile(
    r"(?i)(validate|schema|zod|joi|yup|pydantic|marshmallow|isinstance\(|typeof |\.parse\("
    r"|express-validator|celebrate|class-validator|\bajv\b|wtforms|cerberus|@Valid\b|ValidationError"
    r"|BaseModel|z\.object|Joi\.|validator\.)")


def check_unvalidated_input(path, lines):
    text = "\n".join(lines)
    if VALIDATION_RE.search(text):
        return []
    for i, line in enumerate(lines, 1):
        if REQ_INPUT_RE.search(line) and not is_comment(line):
            return [Hit(i, line.strip(),
                        "Request data is read and used without a visible schema or type check in this file.")]
    return []


NET_RE = re.compile(r"\b(await\s+fetch\(|requests\.(get|post|put|delete|request)\(|urlopen\(|axios\.(get|post|put|delete)\()")
TRY_RE = re.compile(r"\btry\b|^\s*try:")
CATCH_RE = re.compile(r"\bcatch\b|\bexcept\b|\.catch\(")


def check_unhandled_network(path, lines):
    hits = []
    for i, line in enumerate(lines):
        if not NET_RE.search(line) or is_comment(line):
            continue
        before = "\n".join(lines[max(0, i - 3):i])
        after = "\n".join(lines[i:i + 7])
        if TRY_RE.search(before) and CATCH_RE.search(after):
            continue
        if CATCH_RE.search(after) and ".catch(" in after:
            continue
        hits.append(Hit(i + 1, line.strip(), "A network call has no visible error handling around it."))
    return hits


# ---------- LOOSE ENDS ----------

TODO_RE = re.compile(r"(?i)(//|#|/\*|--|<!--)[^\n]*\b(TODO|FIXME|HACK)\b")


def check_todo(path, lines):
    return [Hit(i, l.strip(), "An unresolved TODO/FIXME marker is left in the code.")
            for i, l in enumerate(lines, 1) if TODO_RE.search(l)]


TOOLING = {"eslint", "prettier", "typescript", "jest", "vitest", "mocha", "nodemon", "webpack", "vite",
           "pytest", "black", "ruff", "mypy", "flake8", "pylint", "ts-node", "tsx", "babel", "husky",
           "lint-staged", "concurrently", "next", "react-scripts", "nuxt"}


def _manifest_deps(path, text):
    """Yield (name, line_number) for runtime dependencies of a manifest."""
    base = os.path.basename(path)
    lines = text.splitlines()
    if base == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return
        for name in (data.get("dependencies") or {}):
            if name.startswith("@types/") or name in TOOLING:
                continue
            ln = next((i for i, l in enumerate(lines, 1) if f'"{name}"' in l), 1)
            yield name, ln
    elif base == "requirements.txt":
        for i, l in enumerate(lines, 1):
            s = l.strip()
            if not s or s.startswith(("#", "-")):
                continue
            name = re.split(r"[<>=!~\[; ]", s)[0].strip()
            if name and name.lower() not in TOOLING:
                yield name, i


def check_unused_dependencies(files):
    sources = {p: t for p, t in files.items() if is_source(p)}
    if not sources:
        return []  # no source to judge against: stay silent rather than guess
    out = []
    for path, text in files.items():
        if os.path.basename(path) not in ("package.json", "requirements.txt"):
            continue
        for name, line in _manifest_deps(path, text):
            if os.path.basename(path) == "requirements.txt":
                pattern = re.compile(r"(?im)^\s*(import|from)\s+" + re.escape(name.replace("-", "_")) + r"\b")
                js_pattern = None
            else:
                pattern = None
                js_pattern = re.compile(r"""(from\s+|import\s+|require\(\s*|import\(\s*)['"]""" + re.escape(name) + r"""(/|['"])""")
            used = any((pattern and pattern.search(t)) or (js_pattern and js_pattern.search(t))
                       for t in sources.values())
            if not used:
                out.append((path, Hit(line, f"{name} is declared but no source file imports it.",
                                      "The dependency adds install size and attack surface without a visible use.")))
    return out


ENTRY_NAMES = {"index", "main", "app", "server", "__init__", "cli", "manage", "wsgi", "asgi", "setup",
               "conftest", "config", "settings", "conf"}
# Sample and documentation code is run by hand, so nothing imports it on purpose.
UNIMPORTED_OK_DIRS = {"tests", "test", "scripts", "bin", "__tests__", "docs", "doc", "examples", "example"}


def _module_key(path):
    base = os.path.basename(path)
    name = base.split(".")[0].lower()
    return name


_RUNNABLE_RE = re.compile(
    r"""^#!|^\s*if\s+__name__\s*==\s*["']__main__["']|require\.main\s*===\s*module""", re.M)


def check_dead_modules(files):
    out = []
    srcs = {p: t for p, t in files.items() if is_source(p)}
    for path in srcs:
        key = _module_key(path)
        parts = path.replace("\\", "/").lower().split("/")
        if key in ENTRY_NAMES or key.startswith("test_") or key.endswith(("_test", ".test", ".spec")):
            continue
        if is_test_path(path):
            continue  # test files are run by a test runner, not imported
        if _RUNNABLE_RE.search(files.get(path, "")):
            continue  # a script with a shebang or a main guard is run directly, so nothing imports it by design
        if any(p in UNIMPORTED_OK_DIRS for p in parts[:-1]):
            continue
        pattern = re.compile(r"(?im)(import|from|require|include)[^\n]*\b" + re.escape(key) + r"\b")
        referenced = any(pattern.search(t) for p, t in srcs.items() if p != path)
        if not referenced:
            out.append((path, Hit(1, os.path.basename(path),
                                  "No other source file imports this module by name. Treat as possibly unused.")))
    return out


_BASE_RULES: Tuple[Rule, ...] = (
    Rule("SEC-AUTHZ-001", "security", "Sensitive route registered without an authorization guard", "high", 0.6,
         "Administrative or internal endpoints reachable by any caller expose data and actions to unauthorized users.",
         "Attach the role or authentication middleware to the route and add a test that expects 403 for an unprivileged caller.",
         ("Call the route without credentials and with a non-admin role; both must be rejected.",),
         is_source, check_missing_authz),
    Rule("SEC-SECRET-001", "security", "Hardcoded credential in source or configuration", "critical", 0.9,
         "Anyone with read access to the repository or built bundle can reuse the credential.",
         "Move the value to a server-side environment variable or secret manager, rotate the exposed credential, and add secret scanning to CI.",
         ("Search the repository for the redacted value and confirm it is gone from history.",
          "Confirm the service reads the value from its environment at startup."),
         lambda p: is_text_file(p) and not p.endswith(".env") and not os.path.basename(p).startswith(".env"),
         check_secret),
    Rule("SEC-SECRET-002", "security", "Real-looking secret in a committed env file", "high", 0.8,
         "A committed .env file exposes its values to everyone who can read the repository history.",
         "Remove the file from version control, add it to .gitignore, rotate the value, and commit a .env.example with placeholders.",
         ("Check that .gitignore lists the env file.", "Confirm `git ls-files` no longer contains it."),
         lambda p: ext(p) == ".env", check_env_secret),
    Rule("SEC-SQL-001", "security", "SQL statement built by string concatenation", "high", 0.85,
         "Untrusted input placed into the query text can change its meaning (SQL injection).",
         "Use parameterized queries or a query builder; never join user input into SQL strings.",
         ("Send a quote character in the affected input and confirm the query fails safely.",
          "Add a test that passes a malicious string and asserts it is treated as data."),
         is_source, check_sql),
    Rule("SEC-EVAL-001", "security", "Dynamic code execution with a non-literal argument", "medium", 0.6,
         "Executing a computed string can run attacker-controlled code if any part of it is external.",
         "Replace dynamic evaluation with a lookup table or a parser for the specific format.",
         ("Trace where the evaluated string originates.", "Add a test with hostile input."),
         lambda p: ext(p) in {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}, check_eval),
    Rule("SEC-DEBUG-001", "security", "Debug mode enabled in production configuration", "medium", 0.8,
         "Verbose errors can expose stack traces, paths and internal configuration.",
         "Disable debug in production and make verbose errors depend on an explicit environment variable.",
         ("Load the production profile and confirm debug output is off.",),
         applies_prod_config, check_debug_prod),
    Rule("SLOP-STUB-001", "slop", "Function returns a hardcoded success without performing its operation", "high", 0.75,
         "Callers believe an operation succeeded when no work happened (for example, a payment that never reaches the provider).",
         "Implement the operation and return its real result, or fail closed until it exists.",
         ("Call the function with a failing input and confirm it reports the failure.",
          "Add an integration test against the real dependency or a documented fake."),
         lambda p: is_source(p) and is_production_path(p), check_hardcoded_success),
    Rule("GAP-VALID-001", "gap", "Request input used without a visible validation step", "medium", 0.5,
         "Unchecked input can produce invalid records, unexpected prices or quantities, and crashes.",
         "Validate request bodies against a schema at the boundary and reject unknown or out-of-range fields.",
         ("Send negative, missing and oversized values and confirm each is rejected with a 4xx response.",),
         lambda p: is_source(p) and is_production_path(p), check_unvalidated_input),
    Rule("GAP-ERR-001", "gap", "Network call without visible error handling", "medium", 0.6,
         "A single upstream failure can stop the job silently and leave stale data in place.",
         "Check the response status and wrap the call in try/catch (or except) with a retry or a recorded failure.",
         ("Point the call at an unreachable host and confirm the failure is recorded.",),
         is_source, check_unhandled_network),
    Rule("LOOSE-TODO-001", "loose", "TODO / FIXME marker left in code", "low", 0.99,
         "Unfinished work is shipped without a visible owner or a tracking item.",
         "Resolve the marker or move it into the issue tracker with an owner.",
         ("Search for the marker after the change and confirm none remain in this area.",),
         is_text_file, check_todo),
    Rule("LOOSE-DEP-001", "loose", "Declared dependency is never imported", "low", 0.6,
         "Unused packages increase install size and the number of components to audit.",
         "Remove the dependency, or add the code that is supposed to use it.",
         ("Remove it, then run the test suite and build.",),
         lambda p: os.path.basename(p) in ("package.json", "requirements.txt"), None, check_unused_dependencies, "manifest-scan"),
    Rule("LOOSE-MOD-001", "loose", "Module is never imported", "low", 0.45,
         "Dead modules confuse maintainers and keep outdated assumptions alive.",
         "Delete the module, or wire it into the code path that should use it.",
         ("Search for the module name across the repository to confirm no dynamic loading exists.",),
         is_source, None, check_dead_modules, "import-graph"),
)

# ---- Phase 5 detection: AST-backed Python checks and a cross-file import check ----
from . import ast_rules as _ast  # noqa: E402  (imported here to keep the rule registry readable)


@lru_cache(maxsize=64)
def _ast_hits_for_text(text: str):
    return tuple(_ast.check_python(text))


def _ast_check(rule_id):
    def check(path, lines):
        hits = _ast_hits_for_text("\n".join(lines))
        return [Hit(h.line, redact_line(lines[h.line - 1]) if 0 < h.line <= len(lines) else "",
                    h.explanation, h.end_line)
                for h in hits if h.rule_id == rule_id]
    return check


JS_IMPORT_RE = re.compile(r"""(?:\bfrom\s+|\bimport\s+|\brequire\s*\(\s*)['"](\.{1,2}/[^'"\n]+)['"]""")
PY_RELATIVE_RE = re.compile(r"^\s*from\s+(\.+)([A-Za-z_][\w.]*)\s+import\b")
JS_IMPORT_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".json"}
JS_CANDIDATE_EXTS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".json")


def _js_target_exists(target, files):
    if target in files:
        return True
    if any(target + e in files for e in JS_CANDIDATE_EXTS):
        return True
    return any(target + "/index" + e in files for e in JS_CANDIDATE_EXTS)


def is_test_path(path):
    parts = path.replace("\\", "/").lower().split("/")
    base = parts[-1]
    return (any(p in ("tests", "test", "__tests__", "spec") for p in parts[:-1])
            or base.startswith("test_") or base in ("test.js", "test.ts", "test.py", "tests.py")
            or base.endswith(("_test.py", ".spec.js", ".test.js", ".spec.ts", ".test.ts")))


def is_sample_path(path):
    """Example and documentation code. It is run by hand, so heuristics about production code do not apply."""
    parts = path.replace("\\", "/").lower().split("/")
    return any(p in ("examples", "example", "docs", "doc") for p in parts[:-1])


def is_production_path(path):
    return not is_test_path(path) and not is_sample_path(path)


def check_broken_local_import(files):
    """Relative imports whose target file does not exist in the repository. Asset imports (css, images) are ignored
    because the scanner does not read non-code files, so their absence says nothing about the code."""
    out = []
    directories = set()
    for path in files:
        parts = path.split("/")
        for i in range(1, len(parts)):
            directories.add("/".join(parts[:i]))
    for path, text in files.items():
        base = os.path.dirname(path)
        extension = ext(path)
        lines = text.splitlines()
        for idx, line in enumerate(lines, 1):
            if extension in {".py"}:
                m = PY_RELATIVE_RE.match(line)
                if not m:
                    continue
                up = len(m.group(1)) - 1
                folder = base
                for _ in range(up):
                    folder = os.path.dirname(folder)
                parts = m.group(2).split(".")
                stem = "/".join(p for p in [folder, *parts] if p)
                candidates = {stem + ".py", stem + "/__init__.py"}
                # A directory without __init__.py is a namespace package in Python 3, so it resolves too.
                if not any(c in files for c in candidates) and stem not in directories:
                    out.append((path, Hit(idx, redact_line(line),
                                          f"'from {m.group(1)}{m.group(2)} import' does not resolve to a Python file in this repository.")))
            elif extension in JS_IMPORT_EXTS:
                for m in JS_IMPORT_RE.finditer(line):
                    spec = m.group(1)
                    if os.path.splitext(spec)[1] not in ("", *JS_IMPORT_EXTS):
                        continue  # assets such as stylesheets and images are not part of the scanned set
                    target = os.path.normpath(os.path.join(base, spec)).replace(os.sep, "/")
                    if not _js_target_exists(target, files):
                        out.append((path, Hit(idx, redact_line(line),
                                              f"Relative import '{spec}' does not resolve to a file in this repository.")))
    return out


# Evidence class for each rule. Keeping the mapping here, next to the registry, makes it easy to review.
EVIDENCE_CLASS = {
    "SEC-AUTHZ-001": "potential", "SEC-SECRET-001": "confirmed", "SEC-SECRET-002": "confirmed",
    "SEC-SQL-001": "potential", "SEC-EVAL-001": "potential", "SEC-DEBUG-001": "confirmed",
    "SLOP-STUB-001": "potential", "GAP-VALID-001": "potential", "GAP-ERR-001": "potential",
    "LOOSE-TODO-001": "informational", "LOOSE-DEP-001": "potential", "LOOSE-MOD-001": "potential",
    "LOOSE-STUB-001": "potential", "GAP-ERR-002": "potential", "LOOSE-DEAD-001": "confirmed",
    "GAP-INTEG-001": "confirmed", "SEC-SECRET-003": "confirmed", "SEC-SECRET-004": "confirmed",
    "SEC-CMD-001": "potential", "SEC-XSS-001": "potential", "SEC-DESER-001": "potential", "SEC-PATH-001": "potential",
    "SEC-SSRF-001": "potential", "SEC-CRYPTO-001": "potential",
}

_SECRET_RULES = (
    Rule("SEC-SECRET-003", "security", "Private key committed to the repository", "critical", 0.95,
         "Anyone who can read the repository can use the key to act as the service or user it belongs to.",
         "Remove the key from the repository and from its history, rotate it, and load keys from a secret manager.",
         ("Search the repository history for the PEM header and confirm no copy remains.",),
         is_text_file, check_private_key),
    Rule("SEC-SECRET-004", "security", "Provider access token in source", "critical", 0.9,
         "A live provider token in source lets anyone with read access call the provider as the owner.",
         "Revoke and rotate the token, read it from the environment at runtime, and enable secret scanning.",
         ("Confirm the token is revoked at the provider.", "Confirm the value is read from the environment only."),
         is_text_file, check_token_shapes),
)

_PHASE5_RULES = (
    Rule("LOOSE-STUB-001", "loose", "Function body is a placeholder with no implementation", "medium", 0.9,
         "Callers that rely on this function get no behaviour, and the gap is easy to miss in review.",
         "Implement the function or remove it, and make callers fail explicitly until it exists.",
         ("Call the function from its real caller and confirm the behaviour is implemented.",),
         lambda p: ext(p) == ".py" and is_production_path(p), _ast_check("LOOSE-STUB-001")),
    Rule("GAP-ERR-002", "gap", "Exception handler discards the error", "medium", 0.9,
         "A swallowed exception hides failures, so callers continue with missing or stale data.",
         "Log the exception with context, re-raise it, or record it as a failed result.",
         ("Force the wrapped call to fail and confirm the failure is visible to the caller or in logs.",),
         lambda p: ext(p) == ".py" and is_production_path(p), _ast_check("GAP-ERR-002")),
    Rule("LOOSE-DEAD-001", "loose", "Statement after return or raise can never run", "low", 0.9,
         "Unreachable code suggests an edit went wrong and misleads readers about what the function does.",
         "Delete the unreachable statement or move it before the return.",
         ("Run the function's tests with coverage and confirm the statement is never executed.",),
         lambda p: ext(p) == ".py", _ast_check("LOOSE-DEAD-001")),
    Rule("GAP-INTEG-001", "gap", "Relative import points to a file that does not exist", "high", 0.9,
         "The module fails at import time, or the code path that needs it silently never runs.",
         "Fix the import path, or add the missing module.",
         ("Import the module in a fresh process and confirm it loads.",),
         is_source, None, check_broken_local_import, "import-graph"),
)

_CODE_RULES = (
    Rule("SEC-SSRF-001", "security", "Outbound request URL taken from request input", "high", 0.6,
         "An attacker-chosen URL makes the server call internal services or cloud metadata endpoints (server-side request forgery).",
         "Allowlist the hosts the feature may call, reject private and link-local addresses, and never pass the raw request value as the URL.",
         ("Request a URL that points at an internal address and confirm it is refused.",),
         is_source, check_ssrf),
    Rule("SEC-CRYPTO-001", "security", "Weak hash or cipher primitive", "low", 0.4,
         "MD5 and SHA-1 are weak for collision resistance, and ECB and DES expose plaintext patterns. Often the value is a checksum, cache key or HMAC input, where the weakness does not matter.",
         "Use SHA-256 or stronger for integrity, a dedicated password hash (argon2, bcrypt, scrypt) for passwords, and an authenticated mode such as AES-GCM.",
         ("Confirm the value's purpose: checksum, password, or token. Replace it if it protects secrets.",),
         is_source, check_weak_crypto),
    Rule("SEC-CMD-001", "security", "Shell command built from non-literal input", "high", 0.75,
         "Input that reaches a shell command can run arbitrary commands on the server (command injection).",
         "Pass arguments as a list without a shell, validate the value against an allowlist, and never join input into command strings.",
         ("Send a value containing a shell metacharacter (for example `;` or `$(...)`) and confirm it is treated as data.",),
         is_source, check_command_injection),
    Rule("SEC-XSS-001", "security", "HTML assigned from a non-literal value", "high", 0.7,
         "Untrusted text assigned as markup runs script in the page of every visitor who sees it (cross-site scripting).",
         "Assign text with textContent, or sanitize the markup with a vetted sanitizer before insertion.",
         ("Submit a value containing a script tag and confirm it is displayed as text.",),
         lambda p: ext(p) in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}, check_xss_sink),
    Rule("SEC-DESER-001", "security", "Unsafe deserialization of data", "medium", 0.5,
         "pickle and unsafe YAML loaders can run code when the bytes come from an untrusted party. The same call is lower risk for data a program exchanges with its own child processes.",
         "Use a data-only format such as JSON, or yaml.safe_load, and never unpickle data from a request or an untrusted file.",
         ("Send a crafted payload to the endpoint and confirm it is rejected without executing code.",),
         lambda p: ext(p) == ".py", check_unsafe_deserialization),
    Rule("SEC-PATH-001", "security", "File path built from request input", "high", 0.6,
         "A request-controlled path can read files outside the intended directory (path traversal).",
         "Reduce the value to a basename, join it to a fixed directory, and confirm the resolved path stays inside that directory.",
         ("Request a value such as `../../etc/passwd` and confirm it is refused.",),
         lambda p: is_source(p), check_path_traversal),
)

RULES: Tuple[Rule, ...] = tuple(
    replace(r, evidence_class=EVIDENCE_CLASS[r.id]) for r in _BASE_RULES + _SECRET_RULES + _PHASE5_RULES + _CODE_RULES)
RULES_BY_ID = {r.id: r for r in RULES}
