"""NEXUS INSPECT server: API under /api, health at /health, static frontend from this directory.

Start:  python server.py            (PORT defaults to 8000, HOST to 0.0.0.0)
Data:   NEXUS_DB defaults to data/nexus.sqlite
"""
import json
import logging
import mimetypes
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from nexus import api, auth, events  # noqa: E402
from nexus.store import Store  # noqa: E402

EVENT_STREAM_SECONDS = 240   # a stream closes after this long; EventSource reconnects with Last-Event-ID
EVENT_HEARTBEAT_SECONDS = 15  # idle streams send a named heartbeat this often so proxies and clients see them alive
EVENT_STREAMS_PER_USER = 8    # open live connections allowed per account; each one holds a server thread
EVENT_STREAMS_TOTAL = 64      # open live connections allowed in the whole process
_stream_lock = threading.Lock()
_open_streams = {}
_open_total = 0


def _acquire_stream(user_id):
    global _open_total
    with _stream_lock:
        count = _open_streams.get(user_id, 0)
        if count >= EVENT_STREAMS_PER_USER or _open_total >= EVENT_STREAMS_TOTAL:
            return False
        _open_streams[user_id] = count + 1
        _open_total += 1
        return True


def _release_stream(user_id):
    global _open_total
    with _stream_lock:
        count = _open_streams.get(user_id, 0) - 1
        if count > 0:
            _open_streams[user_id] = count
        else:
            _open_streams.pop(user_id, None)
        _open_total = max(0, _open_total - 1)

# Headers sent with every response. The frontend uses only same-origin scripts, styles, fonts and requests, and no
# inline scripts, so a strict policy can be applied without changing behaviour. Inline style attributes are allowed.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}
PUBLIC_FILES = {"index.html", "styles.css", "home.css", "dash.css", "auth.css", "docs.css", "fonts.css", "assistant.css", "boot.js", "app.js", "home.js", "dash.js", "live.js", "auth.js", "docs.js", "assistant.js"}
# Bundled Geist font files (served from fonts/ only).
PUBLIC_FONTS = {"GeistPixelVF.woff2", "Geist-Regular.woff2", "Geist-Medium.woff2", "Geist-SemiBold.woff2",
                "GeistMono-Regular.woff2", "GeistMono-Medium.woff2"}


def static_response(rel):
    """Serve only whitelisted files from the frontend directory; reject traversal."""
    name = rel.lstrip("/") or "index.html"
    allowed = name in PUBLIC_FILES or (name.startswith("fonts/") and name[len("fonts/"):] in PUBLIC_FONTS)
    if not allowed:
        return 404, {"Content-Type": "application/json"}, b'{"error":{"code":"not_found","message":"Not found."}}'
    full = os.path.realpath(os.path.join(ROOT, name))
    if os.path.commonpath([ROOT, full]) != ROOT or not os.path.isfile(full):
        return 404, {"Content-Type": "application/json"}, b'{"error":{"code":"not_found","message":"Not found."}}'
    ctype = STATIC_TYPES.get(os.path.splitext(full)[1], "application/octet-stream")
    with open(full, "rb") as fh:
        return 200, {"Content-Type": ctype}, fh.read()


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NEXUS/0.1"
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(120)

        def _send(self, status, headers, body):
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _body(self, limit):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if length < 0 or length > limit:
                return None
            return self.rfile.read(length) if length else b""

        def _cross_site_write(self):
            """CSRF guard for cookie-authenticated writes: a browser sends Origin on cross-site requests.
            Requests without a session cookie are not affected, and a missing Origin (non-browser clients) is allowed."""
            if not auth.token_from_headers(dict(self.headers)):
                return False
            origin = self.headers.get("Origin")
            if not origin:
                return False
            return urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower()

        def _events(self):
            """Server-sent event stream for the signed-in user. Each frame is persisted before it is sent, and
            a reconnect replays everything after the client's Last-Event-ID, so no event is lost while disconnected."""
            user = api.current_user(store, dict(self.headers))
            if user is None:
                return self._send(401, {"Content-Type": "application/json"},
                                  b'{"error":{"code":"unauthorized","message":"Sign in to continue."}}')
            if not _acquire_stream(user["id"]):
                return self._send(429, {"Content-Type": "application/json", "Retry-After": "30"},
                                  b'{"error":{"code":"too_many_streams","message":"Too many open live connections. Close another tab and try again."}}')
            try:
                self._stream(user)
            finally:
                _release_stream(user["id"])

        def _stream(self, user):
            cursor = self.headers.get("Last-Event-ID") or parse_qs(urlsplit(self.path).query).get("lastEventId", [""])[0]
            # A first connection starts at the newest event; the dashboard loads current state from the API.
            after = int(cursor) if cursor.isdigit() else store.latest_event_id(user["id"])
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", SECURITY_HEADERS["Content-Security-Policy"])
            self.send_header("Connection", "close")
            self.end_headers()
            deadline = time.monotonic() + EVENT_STREAM_SECONDS
            last_comment = 0.0
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while time.monotonic() < deadline and not self.server.nexus_stop.is_set():
                    batch = store.events_after(user["id"], after)
                    for ev in batch:
                        frame = f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev, sort_keys=True)}\n\n"
                        self.wfile.write(frame.encode("utf-8"))
                        after = ev["id"]
                    if batch:
                        self.wfile.flush()
                        continue
                    now = time.monotonic()
                    if now - last_comment >= EVENT_HEARTBEAT_SECONDS:
                        # A named heartbeat (no id, so the replay cursor does not move) lets the page detect a dead stream.
                        self.wfile.write(b"event: heartbeat\ndata: {}\n\n")
                        self.wfile.flush()
                        last_comment = now
                    events.BROKER.wait(EVENT_HEARTBEAT_SECONDS)
            except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                return  # the client went away; nothing else to clean up because no state is held per connection

        def _dispatch(self, method):
            path = urlsplit(self.path).path
            if path == "/api/events" and method == "GET":
                return self._events()
            if path == "/health" or path.startswith("/api/"):
                if method in ("POST", "PATCH") and self._cross_site_write():
                    return self._send(403, {"Content-Type": "application/json"},
                                      b'{"error":{"code":"cross_site_request","message":"This request came from another site and was refused."}}')
                limit = api.MAX_ZIP_BODY if self.headers.get("Content-Type", "").startswith("application/zip") else api.MAX_JSON_BODY
                body = self._body(limit)
                if body is None:
                    return self._send(413, {"Content-Type": "application/json"},
                                      b'{"error":{"code":"body_too_large","message":"Request body is too large or malformed."}}')
                return self._send(*api.handle(store, method, self.path, body, dict(self.headers),
                                              client_ip=self.client_address[0]))
            if method not in ("GET", "HEAD"):
                return self._send(405, {"Content-Type": "application/json"},
                                  b'{"error":{"code":"method_not_allowed","message":"Method not allowed."}}')
            rel = unquote(path)
            return self._send(*static_response("index.html" if rel in ("/", "") else rel))

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def log_message(self, fmt, *args):
            logging.getLogger("nexus.http").info("%s %s", self.command, self.path.split("?")[0])

    return Handler


def api_cookie_secure_env():
    return auth.cookie_secure()


def build_server(host="0.0.0.0", port=8000, db_path=None):
    db_path = db_path or os.environ.get("NEXUS_DB", os.path.join(ROOT, "data", "nexus.sqlite"))
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    store = Store(db_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    httpd.nexus_stop = threading.Event()  # set on shutdown so open event streams end promptly
    return httpd, store


def stop_server(httpd):
    """Stop accepting requests, end open event streams, then close the listening socket."""
    httpd.nexus_stop.set()
    events.BROKER.notify()
    httpd.shutdown()
    httpd.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    httpd, _ = build_server(host, port)
    if not api_cookie_secure_env():
        logging.getLogger("nexus").warning(
            "Session cookies are not marked Secure. Set NEXUS_COOKIE_SECURE=1 when the site is served over HTTPS.")

    def _terminate(signum, frame):  # stop cleanly on SIGTERM as well as Ctrl+C
        threading.Thread(target=stop_server, args=(httpd,), daemon=True).start()

    signal.signal(signal.SIGTERM, _terminate)
    print(f"NEXUS INSPECT listening on http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
