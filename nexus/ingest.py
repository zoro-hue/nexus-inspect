"""Repository ingestion: GitHub URL validation, guarded downloads, and safe ZIP extraction.

Repository content is untrusted. Extraction rejects absolute paths, parent-directory segments,
symlinks, and archives that expand beyond fixed limits. Downloads are limited to HTTPS requests
to an allowlist of GitHub hosts, and every redirect is checked against the same allowlist.
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACT_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_RATIO = 100
RATIO_MIN_SIZE = 1024 * 1024
ALLOWED_HOSTS = {"github.com", "codeload.github.com"}
API_HOSTS = {"api.github.com"}
MAX_BRANCHES = 100
GITHUB_URL_RE = re.compile(r"^(?:https://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")

_INSPECT_CACHE = {}
_CACHE_TTL = 300  # seconds (5 minutes)
_CACHE_LOCK = threading.Lock()


def get_github_token() -> str:
    """Return GITHUB_TOKEN from environment if set, otherwise empty string."""
    return os.environ.get("GITHUB_TOKEN", "").strip()


def clear_inspect_cache():
    """Clear in-memory inspect cache (useful for testing)."""
    with _CACHE_LOCK:
        _INSPECT_CACHE.clear()


class IngestError(Exception):
    def __init__(self, code, message, status=422, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


def parse_github_url(url: str):
    """Return (owner, repo). Only public github.com repository roots are accepted."""
    if not isinstance(url, str) or not url.strip():
        raise IngestError("invalid_url", "Enter a GitHub repository URL, for example github.com/owner/repo.")
    m = GITHUB_URL_RE.match(url.strip())
    if not m:
        raise IngestError("invalid_url", "Only public github.com repository roots are supported (owner/repo).")
    owner, repo = m.group(1), m.group(2)
    if repo in (".", "..") or owner in (".", ".."):
        raise IngestError("invalid_url", "The repository name is not valid.")
    return owner, repo


def validate_branch(branch: str) -> str:
    if not branch:
        return "HEAD"
    if not BRANCH_RE.match(branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise IngestError("invalid_branch", "The branch name contains characters that are not allowed.")
    return branch


class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_allowed_url(newurl, ALLOWED_HOSTS | API_HOSTS)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_allowed_url(url: str, hosts=ALLOWED_HOSTS):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in hosts:
        raise IngestError("blocked_host", "The download host is not on the allowlist.", 502)


def download_repository_archive(url_or_repo: str, branch: str, opener=None, timeout=20):
    """Fetch a repository ZIP from GitHub. Returns raw bytes capped at MAX_UPLOAD_BYTES."""
    owner, repo = parse_github_url(url_or_repo)
    branch = validate_branch(branch)
    # "HEAD" means the repository's default branch; codeload serves it at zip/HEAD.
    ref = "HEAD" if branch == "HEAD" else "refs/heads/" + urllib.parse.quote(branch, safe="/")
    archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
    check_allowed_url(archive_url)
    opener = opener or urllib.request.build_opener(_AllowlistRedirect)
    try:
        with opener.open(archive_url, timeout=timeout) as resp:
            data = resp.read(MAX_UPLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IngestError("repository_not_found", f"{owner}/{repo} on branch {branch} was not found or is private.", 404)
        if exc.code in (403, 429):
            raise IngestError("rate_limited", "GitHub rate limit reached. Wait a few minutes and try again.", 502)
        raise IngestError("upstream_error", f"GitHub responded with HTTP {exc.code}.", 502)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise IngestError("upstream_unavailable", "GitHub could not be reached. Try again later or upload a ZIP.", 502)
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError("archive_too_large", "The repository archive is larger than the 25 MB limit.", 413)
    return data


def _unsafe_name(name: str) -> bool:
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return True
    return any(part == ".." for part in n.split("/"))


def extract_zip(data: bytes, dest: str) -> str:
    """Safely extract a ZIP into dest and return the repository root path."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError("archive_too_large", "The archive is larger than the 25 MB limit.", 413)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise IngestError("invalid_archive", "The file is not a valid ZIP archive.")
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise IngestError("too_many_entries", f"The archive has more than {MAX_ENTRIES} entries.", 413)
    declared = sum(i.file_size for i in infos)
    if declared > MAX_EXTRACT_BYTES:
        raise IngestError("archive_too_large", "The archive expands beyond the 100 MB limit.", 413)

    root_real = os.path.realpath(dest)
    written = 0
    files = []
    for info in infos:
        name = info.filename.replace("\\", "/")
        if _unsafe_name(name):
            raise IngestError("unsafe_path", f"Archive entry has an unsafe path: {name[:120]!r}.")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise IngestError("symlink_in_archive", "Archives containing symbolic links are rejected.")
        if info.is_dir() or name.endswith("/"):
            continue
        if info.file_size > RATIO_MIN_SIZE and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise IngestError("suspicious_compression", "An entry compresses implausibly well and was rejected.")
        target = os.path.realpath(os.path.join(dest, *name.split("/")))
        if os.path.commonpath([root_real, target]) != root_real:
            raise IngestError("unsafe_path", "An entry would be written outside the working directory.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_EXTRACT_BYTES:
                    raise IngestError("archive_too_large", "The archive expands beyond the 100 MB limit.", 413)
                out.write(chunk)
        files.append(name)
    if not files:
        raise IngestError("empty_repository", "The archive contains no files to analyze.")
    tops = {f.split("/")[0] for f in files}
    if len(tops) == 1 and all("/" in f for f in files):
        return os.path.join(dest, tops.pop())  # GitHub archives wrap everything in one folder
    return dest


def clone_public_repo(url: str, branch: str, dest_dir: str, timeout: int = 60) -> str:
    """Safely clone a public repository branch into dest_dir without executing any repo code."""
    owner, repo = parse_github_url(url)
    branch = validate_branch(branch)
    repo_url = f"https://github.com/{owner}/{repo}.git"
    target_dir = os.path.join(dest_dir, f"{repo}")
    os.makedirs(dest_dir, exist_ok=True)

    cmd = [
        "git",
        "-c", "credential.helper=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "protocol.file.allow=never",
        "clone",
        "--depth", "1",
        "--single-branch",
    ]
    if branch and branch != "HEAD":
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, target_dir])

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        raise IngestError("timeout", "Git clone timed out.", 504)
    except OSError as exc:
        raise IngestError("git_error", f"Failed to execute git: {exc}", 502)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        err_lower = err.lower()
        if "remote branch" in err_lower and "not found" in err_lower:
            raise IngestError("invalid_branch", f"Branch '{branch}' was not found in the repository.", 404)
        if "could not read username" in err_lower or "authentication failed" in err_lower or "terminal prompts disabled" in err_lower:
            raise IngestError("private_repository", "The repository is private or requires authorization. Only public repositories can be inspected.", 403)
        if "repository not found" in err_lower or "not found" in err_lower:
            raise IngestError("repository_not_found", "The repository was not found or is private.", 404)
        raise IngestError("git_error", f"Git clone failed: {err[:200]}", 502)

    # Security: strip the .git directory so no hooks, configs, or git state can execute or be scanned
    git_dir = os.path.join(target_dir, ".git")
    if os.path.exists(git_dir):
        def _remove_readonly(func, path, exc_info):
            try:
                os.chmod(path, 0o777)
                func(path)
            except Exception:
                pass
        shutil.rmtree(git_dir, onerror=_remove_readonly)

    # Verify limits: file count and extract size, and reject symlinks
    total_size = 0
    total_files = 0
    for root, dirs, files in os.walk(target_dir):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.islink(fp):
                os.unlink(fp)
                continue
            total_files += 1
            if total_files > MAX_ENTRIES:
                raise IngestError("too_many_entries", f"The repository has more than {MAX_ENTRIES} files.", 413)
            try:
                total_size += os.path.getsize(fp)
            except OSError:
                pass
            if total_size > MAX_EXTRACT_BYTES:
                raise IngestError("archive_too_large", "The repository expands beyond the 100 MB limit.", 413)

    if total_files == 0:
        raise IngestError("empty_repository", "The repository contains no files to analyze.")

    return target_dir


def fetch_repository_source(url_or_repo: str, branch: str, work_dir: str, opener=None, timeout=30) -> str:
    """Obtain repository contents either via zip archive or safe Git clone fallback."""
    try:
        archive = download_repository_archive(url_or_repo, branch, opener=opener, timeout=timeout)
        return extract_zip(archive, work_dir)
    except IngestError as exc:
        # Fall back to safe Git clone if archive download failed due to rate limiting or upstream error
        if opener is None and exc.code in ("rate_limited", "upstream_error", "upstream_unavailable"):
            return clone_public_repo(url_or_repo, branch, work_dir, timeout=timeout * 2)
        raise


def git_ls_remote(url: str, timeout: int = 15) -> dict:
    """Retrieve default branch and branch list using Git transport without hitting GitHub REST API."""
    owner, repo = parse_github_url(url)
    repo_url = f"https://github.com/{owner}/{repo}.git"
    cmd = [
        "git",
        "-c", "credential.helper=",
        "-c", "core.hooksPath=/dev/null",
        "ls-remote",
        "--symref",
        repo_url,
        "HEAD",
        "refs/heads/*",
    ]
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        raise IngestError("timeout", "Git transport timed out while connecting to GitHub. Try again later or upload a ZIP.", 504)
    except OSError as exc:
        raise IngestError("git_error", f"Failed to execute git: {exc}", 502)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        err_lower = err.lower()
        if "repository not found" in err_lower or "not found" in err_lower:
            raise IngestError("repository_not_found", "The repository was not found or is private.", 404)
        if "could not read username" in err_lower or "authentication failed" in err_lower or "terminal prompts disabled" in err_lower:
            raise IngestError("private_repository", "The repository is private or requires authorization. Only public repositories can be inspected.", 403)
        raise IngestError("git_error", f"Git transport failed: {err[:200]}", 502)

    default_branch = None
    branches = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("ref: refs/heads/") and "\tHEAD" in line:
            ref_part = line.split("\t")[0]
            default_branch = ref_part.replace("ref: refs/heads/", "").strip()
        elif "\trefs/heads/" in line:
            parts = line.split("\trefs/heads/")
            if len(parts) == 2:
                b_name = parts[1].strip()
                if b_name and b_name not in branches:
                    branches.append(b_name)

    if not default_branch:
        if "main" in branches:
            default_branch = "main"
        elif "master" in branches:
            default_branch = "master"
        elif branches:
            default_branch = branches[0]
        else:
            default_branch = "HEAD"

    if default_branch and default_branch != "HEAD" and default_branch not in branches:
        branches.insert(0, default_branch)

    return {
        "owner": owner,
        "repo": repo,
        "defaultBranch": default_branch,
        "branches": branches[:MAX_BRANCHES],
    }


def _api_json(url, opener=None, timeout=15):
    check_allowed_url(url, API_HOSTS)
    opener = opener or urllib.request.build_opener(_AllowlistRedirect)
    headers = {
        "User-Agent": "NEXUS-INSPECT",
        "Accept": "application/vnd.github+json",
    }
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if hasattr(opener, "addheaders") and isinstance(opener.addheaders, list):
        existing_names = {h[0].lower() for h in opener.addheaders}
        for k, v in headers.items():
            if k.lower() not in existing_names:
                opener.addheaders.append((k, v))

    if isinstance(opener, urllib.request.OpenerDirector) or getattr(opener, "accepts_request", False):
        target = urllib.request.Request(url, headers=headers)
    else:
        target = url

    try:
        with opener.open(target, timeout=timeout) as resp:
            data = resp.read(256 * 1024)
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            pass

        if exc.code == 401:
            raise IngestError("invalid_token", "GitHub token is invalid or expired. Check GITHUB_TOKEN.", 401)

        if exc.code in (403, 429):
            if "bad credentials" in body_text.lower():
                raise IngestError("invalid_token", "GitHub token is invalid. Check GITHUB_TOKEN.", 401)

            resp_hdrs = getattr(exc, "headers", {}) or {}
            remaining = resp_hdrs.get("X-RateLimit-Remaining") if hasattr(resp_hdrs, "get") else None
            reset_ts = resp_hdrs.get("X-RateLimit-Reset") if hasattr(resp_hdrs, "get") else None
            retry_after = resp_hdrs.get("Retry-After") if hasattr(resp_hdrs, "get") else None

            is_rate_limit = (
                exc.code == 429
                or remaining == "0"
                or "rate limit" in body_text.lower()
                or "rate limit" in str(exc).lower()
                or "api rate limit" in getattr(exc, "msg", "").lower()
            )

            if is_rate_limit:
                retry_sec = None
                if retry_after and str(retry_after).isdigit():
                    retry_sec = int(retry_after)
                elif reset_ts and str(reset_ts).isdigit():
                    retry_sec = max(1, int(int(reset_ts) - time.time()))

                msg = "GitHub rate limit reached. Wait a few minutes and try again."
                if retry_sec:
                    msg = f"GitHub rate limit reached. Retry in {retry_sec}s or configure GITHUB_TOKEN."
                raise IngestError("rate_limited", msg, 429 if exc.code == 429 else 502, retry_after=retry_sec)

            if "permission" in body_text.lower() or "resource not accessible" in body_text.lower():
                raise IngestError("insufficient_permissions", "GitHub token does not have sufficient permissions to access this repository.", 403)

            raise IngestError("private_repository", "The repository is private or requires authorization. Only public repositories can be inspected.", 403)

        if exc.code == 404:
            raise IngestError("repository_not_found", "The repository was not found or is private.", 404)

        raise IngestError("upstream_error", f"GitHub responded with HTTP {exc.code}.", 502)
    except (TimeoutError, socket.timeout):
        raise IngestError("timeout", "GitHub request timed out. Check your connection or try again later.", 504)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower():
            raise IngestError("timeout", "GitHub request timed out. Check your connection or try again later.", 504)
        raise IngestError("upstream_unavailable", "GitHub could not be reached. Try again later or upload a ZIP.", 502)
    except (OSError, ValueError):
        raise IngestError("upstream_unavailable", "GitHub could not be reached. Try again later or upload a ZIP.", 502)


def inspect_repository(url: str, opener=None, fallback=True):
    """Return the default branch and up to MAX_BRANCHES branch names for a public repository."""
    owner, repo = parse_github_url(url)
    cache_key = f"{owner.lower()}/{repo.lower()}"

    if opener is None:
        now = time.time()
        with _CACHE_LOCK:
            cached = _INSPECT_CACHE.get(cache_key)
            if cached and cached["expires_at"] > now:
                return dict(cached["data"])

    base = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        info = _api_json(base, opener)
        branches = _api_json(f"{base}/branches?per_page={MAX_BRANCHES}", opener)
        names = [b["name"] for b in branches if isinstance(b, dict) and isinstance(b.get("name"), str)]
        result = {
            "owner": owner,
            "repo": repo,
            "defaultBranch": info.get("default_branch") or "HEAD",
            "branches": names[:MAX_BRANCHES],
        }
        if opener is None:
            with _CACHE_LOCK:
                _INSPECT_CACHE[cache_key] = {"data": result, "expires_at": time.time() + _CACHE_TTL}
        return result
    except IngestError as exc:
        if exc.code == "rate_limited" and fallback:
            try:
                res = git_ls_remote(url)
                if opener is None:
                    with _CACHE_LOCK:
                        _INSPECT_CACHE[cache_key] = {"data": res, "expires_at": time.time() + _CACHE_TTL}
                return res
            except IngestError as git_exc:
                if git_exc.code in ("repository_not_found", "private_repository"):
                    raise git_exc
                raise exc
        raise
