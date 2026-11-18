"""Test helpers: an authenticated call wrapper so existing API tests run as a signed-in user."""
import uuid

from nexus import api, auth


def session_cookie(store):
    """Create (once per store) a test account and session, and return its Cookie header value."""
    cookie = getattr(store, "_test_cookie", None)
    if cookie is None:
        uid = store.create_user(f"tester-{uuid.uuid4().hex[:8]}@example.com", "Tester",
                                auth.hash_password("correct-horse-1"))
        token, digest = auth.new_token()
        store.create_session(digest, uid, auth.expiry_iso())
        cookie = f"{auth.SESSION_COOKIE}={token}"
        store._test_cookie = cookie
        store._test_user_id = uid
    return cookie


def handle(store, method, path, body=b"", headers=None, opener=None):
    """Like api.handle, but sends the test account's session cookie."""
    merged = dict(headers or {})
    merged["Cookie"] = session_cookie(store)
    return api.handle(store, method, path, body, merged, opener=opener)


def user_id(store):
    session_cookie(store)
    return store._test_user_id
