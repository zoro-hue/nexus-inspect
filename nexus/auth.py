"""Account authentication: PBKDF2 password hashing, opaque session tokens stored as SHA-256 digests,
HttpOnly session cookies, and in-memory sign-in throttling. Standard library only.

Limitations (documented in README): the throttle is per process and resets on restart; there is no
email verification or password reset; OAuth is not implemented.
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 310_000
SESSION_DAYS = 7
SESSION_COOKIE = "nexus_session"
MIN_PASSWORD = 10
MAX_PASSWORD = 128
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_DUMMY_HASH = None

_failures = {}
_failures_lock = threading.Lock()
THROTTLE_LIMIT = 5
THROTTLE_WINDOW = 15 * 60


class AuthError(Exception):
    def __init__(self, status, code, message, fields=None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


def validate_signup(email, name, password):
    """Return normalized (email, name). Raises AuthError(422) with per-field messages."""
    fields = {}
    email = (email or "").strip().lower() if isinstance(email, str) else ""
    if not email or len(email) > 254 or not EMAIL_RE.match(email):
        fields["email"] = "Enter a valid email address."
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 80:
        fields["name"] = "Enter a name between 1 and 80 characters."
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        fields["password"] = f"Use at least {MIN_PASSWORD} characters."
    elif len(password) > MAX_PASSWORD:
        fields["password"] = f"Use at most {MAX_PASSWORD} characters."
    elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        fields["password"] = "Use at least one letter and one number."
    if fields:
        raise AuthError(422, "validation_failed", "Check the highlighted fields.", fields)
    return email, name


def validate_signin(email, password):
    fields = {}
    if not isinstance(email, str) or not email.strip():
        fields["email"] = "Enter your email address."
    if not isinstance(password, str) or not password:
        fields["password"] = "Enter your password."
    if fields:
        raise AuthError(422, "validation_failed", "Check the highlighted fields.", fields)
    return email.strip().lower()


def _throttle_key(email):
    return email.lower()


def check_throttle(email):
    now = time.monotonic()
    with _failures_lock:
        recent = [t for t in _failures.get(_throttle_key(email), []) if now - t < THROTTLE_WINDOW]
        _failures[_throttle_key(email)] = recent
        if len(recent) >= THROTTLE_LIMIT:
            raise AuthError(429, "too_many_attempts", "Too many failed sign-in attempts. Wait 15 minutes and try again.")


MAX_TRACKED_EMAILS = 10000


def record_failure(email):
    now = time.monotonic()
    with _failures_lock:
        if len(_failures) >= MAX_TRACKED_EMAILS:
            # Drop entries whose window has passed; if the table is still full, drop the oldest keys.
            for key in [k for k, v in _failures.items() if not v or now - v[-1] >= THROTTLE_WINDOW]:
                _failures.pop(key, None)
            while len(_failures) >= MAX_TRACKED_EMAILS:
                _failures.pop(next(iter(_failures)), None)
        _failures.setdefault(_throttle_key(email), []).append(now)


def clear_failures(email):
    with _failures_lock:
        _failures.pop(_throttle_key(email), None)


def new_token():
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def expiry_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cookie_secure():
    return os.environ.get("NEXUS_COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def set_cookie_header(token):
    parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_DAYS * 86400}"]
    if cookie_secure():
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header():
    parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if cookie_secure():
        parts.append("Secure")
    return "; ".join(parts)


def token_from_headers(headers):
    raw = next((v for k, v in (headers or {}).items() if k.lower() == "cookie"), "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            return value
    return None


def burn_dummy_hash(password):
    """Spend comparable time on unknown emails so response timing does not reveal account existence."""
    verify_password(password or "", _dummy_hash())
