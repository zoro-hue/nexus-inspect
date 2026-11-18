"""Single-file data-flow helpers: request-input taint tracking and language-aware sink detection.

Pure functions over source lines. They never execute repository code and never import the rule
registry. Each finding names the line where request input entered the value (the source line),
so a reviewer can check both ends of the flow.

Limits: tracking is sequential within one file. It does not follow values across files, through
object fields, function parameters, or aliases, and it does not model loops. A reassignment from a
known sanitizer or a literal clears the taint.
"""
import re

# Where untrusted input enters: web frameworks (Python, JavaScript), Go net/http, Java servlets, PHP superglobals.
SOURCE_RE = re.compile(
    r"\b(request|req)\.(args|GET|POST|query|params|body|form|files|json|data|values|cookies)\b"
    r"|\br\.(URL\.Query\(\)|FormValue\(|PostFormValue\()"
    r"|\brequest\.getParameter\("
    r"|\$_(GET|POST|REQUEST|COOKIE)\b"
)
# name = expression, or a typed declaration (const/let/var/final/String/string). Excludes ==, =>, and member targets.
ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var|final|String|string)\s+)?([A-Za-z_]\w*)\s*(?::=|=(?![=>]))\s*(.+?);?\s*$")
# Values passed through one of these are no longer attacker-controlled for the sink types checked here.
SANITIZER_RE = re.compile(
    r"\b(basename|secure_filename|realpath|normpath|resolve|commonpath|shlex\.quote|escapeshellarg|"
    r"encodeURIComponent|parseInt|int|float|quote|isNaN)\s*\(|\bNumber\(")


def _names_in(text, names):
    return [n for n in names if re.search(r"\b%s\b" % re.escape(n), text)]


def taint_state_by_line(lines):
    """Return {line_number: {name: source_line}}: the names carrying request input just before each line."""
    state = {}
    out = {}
    for i, line in enumerate(lines, 1):
        out[i] = dict(state)
        m = ASSIGN_RE.match(line)
        if not m:
            continue
        name, rhs = m.group(1), m.group(2)
        if SANITIZER_RE.search(rhs):
            state.pop(name, None)
        elif SOURCE_RE.search(rhs):
            state[name] = i
        else:
            used = _names_in(rhs, state)
            if used:
                state[name] = min(state[u] for u in used)
            else:
                state.pop(name, None)
    return out


def _tainted_sources(line, state):
    """Source lines of request input reaching this line, directly or through tainted names."""
    sources = set()
    if SOURCE_RE.search(line):
        sources.add(None)  # request input used directly on this line
    for name in _names_in(line, state):
        sources.add(state[name])
    return sources


def _origin(sources, line_no):
    """Where the input came from: this line if it is used directly here, else the earliest tainting line."""
    if None in sources:
        return line_no
    return min(sources)


def _where(src, line_no):
    return "on this line" if src == line_no else f"from line {src}"


# ---- sinks: each returns [(line_number, explanation)] ----

_CMD_SINK = {
    ".py": re.compile(r"\bsubprocess\.(run|call|Popen|check_output|check_call|getoutput)\(|\bos\.(system|popen)\("),
    ".js": re.compile(r"(?<![\w.])exec(Sync)?\(|\bchild_process\b.*\.exec(Sync)?\("),
    ".go": re.compile(r"\bexec\.Command\(\s*\"(sh|bash)\""),
    ".java": re.compile(r"\bRuntime\.getRuntime\(\)\.exec\("),
    ".php": re.compile(r"\b(system|exec|shell_exec|passthru|popen|proc_open)\s*\("),
}
_ALIASES = {".ts": ".js", ".jsx": ".js", ".tsx": ".js", ".mjs": ".js", ".cjs": ".js"}


def _lang(ext):
    return _ALIASES.get(ext, ext)


def command_flow_hits(ext, lines):
    """Tainted values in a command sink, plus concatenation in Go, Java and PHP sinks written directly."""
    rx = _CMD_SINK.get(_lang(ext))
    if rx is None:
        return []
    state = taint_state_by_line(lines)
    out = []
    for i, line in enumerate(lines, 1):
        if not rx.search(line):
            continue
        sources = _tainted_sources(line, state[i])
        if sources:
            where = _where(_origin(sources, i), i)
            out.append((i, f"Request input reaches this command {where} without a sanitizer."))
            continue
        if _lang(ext) == ".go" and re.search(r"\"\s*\+|\+\s*\"", line):
            out.append((i, "A shell command is assembled by concatenation inside `sh -c`."))
        elif _lang(ext) == ".java" and "+" in line:
            out.append((i, "`Runtime.exec` receives a string built by concatenation. Use ProcessBuilder with an argument list."))
        elif _lang(ext) == ".php" and re.search(r"\.\s*\$|\$\w+\s*\.", line):
            out.append((i, "A PHP shell function receives a variable concatenated into its command."))
    return out


_SSRF_SINK = {
    ".py": re.compile(r"\b(requests\.(get|post|put|delete|head|request)|httpx\.(get|post)|urlopen)\("),
    ".js": re.compile(r"\b(axios\.(get|post|put|delete|request)|fetch|got|http\.get|https\.get)\("),
    ".go": re.compile(r"\bhttp\.(Get|Post|Head)\("),
    ".java": re.compile(r"\bnew URL\("),
    ".php": re.compile(r"\b(file_get_contents|curl_setopt)\("),
}


def ssrf_flow_hits(ext, lines):
    """A request URL taken from request input (directly or through a variable) in an outbound HTTP call."""
    rx = _SSRF_SINK.get(_lang(ext))
    if rx is None:
        return []
    state = taint_state_by_line(lines)
    out = []
    for i, line in enumerate(lines, 1):
        m = rx.search(line)
        if not m:
            continue
        args = line[m.end():]
        sources = _tainted_sources(args, state[i])
        if sources:
            where = _where(_origin(sources, i), i)
            out.append((i, f"The URL of this outbound request comes from request input {where}."))
    return out


_PATH_SINK = {
    ".py": re.compile(r"\b(open|send_file|send_from_directory)\("),
    ".js": re.compile(r"\b(readFile|readFileSync|createReadStream|sendFile|download)\("),
    ".go": re.compile(r"\b(os\.Open|ioutil\.ReadFile|os\.ReadFile)\("),
    ".java": re.compile(r"\bnew FileInputStream\(|\bPaths\.get\("),
    ".php": re.compile(r"\b(file_get_contents|fopen|include|require)\s*\("),
}


def path_flow_hits(ext, lines):
    """A file-access call whose path carries request input (directly or through variables)."""
    rx = _PATH_SINK.get(_lang(ext))
    if rx is None:
        return []
    state = taint_state_by_line(lines)
    out = []
    for i, line in enumerate(lines, 1):
        m = rx.search(line)
        if not m:
            continue
        sources = _tainted_sources(line[m.end():], state[i])
        if sources:
            where = _where(_origin(sources, i), i)
            out.append((i, f"The file path comes from request input {where} with no basename or containment check."))
    return out
