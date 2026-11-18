"""Server-sent events over a real socket: authentication, ordering, replay, isolation, tabs, payload safety, cleanup."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

import server
from tests.test_store_api import VULN

BASE_HEADERS = {"Content-Type": "application/json"}


def read_frames(resp, until=None, limit_seconds=8.0):
    """Read SSE frames from an open response until an event type in `until` arrives or time runs out."""
    frames, current = [], {}
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        line = resp.readline()
        if not line:
            break
        line = line.decode("utf-8").rstrip("\n")
        if line == "":
            # Heartbeats are liveness signals, not analysis events; the tests examine data events only.
            if current.get("event") and current["event"] != "heartbeat":
                frames.append(current)
                if until and current["event"] in until:
                    break
            current = {}
        elif line.startswith(":"):
            continue
        else:
            key, _, value = line.partition(": ")
            current[key] = value
    return frames


class EventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.httpd, self.store = server.build_server("127.0.0.1", 0, os.path.join(self.tmp, "e.sqlite"))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        server.stop_server(self.httpd)
        self.thread.join(timeout=5)
        self.store.close()

    def call(self, method, path, body=None, cookie=None, ctype=None):
        headers = {"Content-Type": ctype or "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def sign_up(self):
        status, headers, _ = self.call("POST", "/api/auth/signup", {
            "email": f"ev-{uuid.uuid4().hex[:8]}@example.com", "name": "Ev", "password": "correct-horse-1"})
        self.assertEqual(status, 201)
        return headers["Set-Cookie"].split(";", 1)[0]

    def subscribe(self, cookie, last_event_id=None):
        headers = {"Cookie": cookie, "Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        req = urllib.request.Request(self.base + "/api/events", headers=headers)
        return urllib.request.urlopen(req, timeout=20)

    def upload(self, cookie, name="repo"):
        return self.call("POST", f"/api/analyses?name={name}", VULN, cookie=cookie, ctype="application/zip")

    # ----- liveness -----
    def test_heartbeat_is_a_named_event_that_does_not_move_the_replay_cursor(self):
        cookie = self.sign_up()
        old = server.EVENT_HEARTBEAT_SECONDS
        server.EVENT_HEARTBEAT_SECONDS = 0.2
        found = None
        try:
            resp = self.subscribe(cookie)
            block, deadline = [], time.monotonic() + 5
            while time.monotonic() < deadline and found is None:
                line = resp.readline().decode("utf-8").rstrip("\n")
                if line == "":
                    if block and block[0] == "event: heartbeat":
                        found = block
                    block = []
                else:
                    block.append(line)
            resp.close()
        finally:
            server.EVENT_HEARTBEAT_SECONDS = old
        self.assertIsNotNone(found, "no heartbeat arrived")
        self.assertIn("data: {}", found)
        self.assertFalse(any(line.startswith("id:") for line in found))

    def test_stream_count_is_capped_per_account_and_other_accounts_are_unaffected(self):
        cookie = self.sign_up()
        other = self.sign_up()
        opened = []
        try:
            for _ in range(server.EVENT_STREAMS_PER_USER):
                opened.append(self.subscribe(cookie))
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.subscribe(cookie)
            self.assertEqual(ctx.exception.code, 429)
            self.assertEqual(ctx.exception.headers.get("Retry-After"), "30")
            self.assertEqual(json.loads(ctx.exception.read())["error"]["code"], "too_many_streams")
            # Another account on the same server keeps its own allowance.
            third = self.subscribe(other)
            self.assertEqual(third.status, 200)
            third.close()
        finally:
            for resp in opened:
                resp.close()

    # ----- authentication -----
    def test_stream_requires_a_session(self):
        status, _, body = self.call("GET", "/api/events")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    # ----- ordering and persistence -----
    def test_analysis_lifecycle_streams_in_order_and_ends_with_completed(self):
        cookie = self.sign_up()
        resp = self.subscribe(cookie)
        status, _, body = self.upload(cookie)
        self.assertIn(status, (201, 422))
        frames = read_frames(resp, until={"analysis.completed", "analysis.failed"})
        resp.close()
        types = [f["event"] for f in frames]
        for expected in ("project.created", "analysis.queued", "analysis.started", "analysis.stage_updated",
                         "analysis.completed"):
            self.assertIn(expected, types)
        self.assertLess(types.index("analysis.queued"), types.index("analysis.started"))
        self.assertLess(types.index("analysis.started"), types.index("analysis.completed"))
        self.assertEqual(types[-1], "analysis.completed")
        self.assertIn("finding.created", types)
        ids = [int(f["id"]) for f in frames]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_stage_events_name_real_stages_and_carry_no_percentage(self):
        cookie = self.sign_up()
        resp = self.subscribe(cookie)
        self.upload(cookie)
        frames = read_frames(resp, until={"analysis.completed", "analysis.failed"})
        resp.close()
        stages = [json.loads(f["data"])["data"]["stage"] for f in frames if f["event"] == "analysis.stage_updated"]
        self.assertIn("security_and_quality", stages)
        for f in frames:
            self.assertNotIn("progress", json.loads(f["data"])["data"])

    def test_completed_event_totals_match_the_persisted_findings(self):
        cookie = self.sign_up()
        resp = self.subscribe(cookie)
        _, _, body = self.upload(cookie)
        aid = json.loads(body)["id"]
        frames = read_frames(resp, until={"analysis.completed", "analysis.failed"})
        resp.close()
        completed = json.loads([f for f in frames if f["event"] == "analysis.completed"][-1]["data"])["data"]
        persisted = self.store.list_findings(aid)
        self.assertEqual(completed["findingCount"], len(persisted))
        created = [f for f in frames if f["event"] == "finding.created"]
        self.assertEqual(len(created), len(persisted))

    def test_finding_status_change_publishes_an_update_or_resolution(self):
        cookie = self.sign_up()
        _, _, body = self.upload(cookie)
        aid = json.loads(body)["id"]
        fid = self.store.list_findings(aid)[0]["id"]
        resp = self.subscribe(cookie)  # starts at the newest event
        self.call("PATCH", f"/api/analyses/{aid}/findings/{fid}", {"status": "fixed"}, cookie=cookie)
        frames = read_frames(resp, until={"finding.resolved", "finding.updated"}, limit_seconds=5)
        resp.close()
        self.assertEqual([f["event"] for f in frames], ["finding.resolved"])
        self.assertEqual(json.loads(frames[0]["data"])["data"]["status"], "fixed")

    def test_report_export_publishes_report_generated(self):
        cookie = self.sign_up()
        _, _, body = self.upload(cookie)
        aid = json.loads(body)["id"]
        resp = self.subscribe(cookie)
        self.call("GET", f"/api/analyses/{aid}/report?format=sarif", cookie=cookie)
        frames = read_frames(resp, until={"report.generated"}, limit_seconds=5)
        resp.close()
        self.assertEqual(frames[-1]["event"], "report.generated")
        self.assertEqual(json.loads(frames[-1]["data"])["data"]["format"], "sarif")

    # ----- reconnection and replay -----
    def test_reconnect_replays_everything_after_last_event_id(self):
        cookie = self.sign_up()
        self.upload(cookie)
        first = read_frames(self.subscribe(cookie, last_event_id=0), until={"analysis.completed"})
        self.assertTrue(first)
        cut = int(first[len(first) // 2]["id"])
        replay = read_frames(self.subscribe(cookie, last_event_id=cut), until={"analysis.completed"})
        self.assertEqual([int(f["id"]) for f in replay], [int(f["id"]) for f in first if int(f["id"]) > cut])

    def test_events_during_disconnect_are_not_lost(self):
        cookie = self.sign_up()
        resp = self.subscribe(cookie)
        resp.close()  # the tab goes away
        self.upload(cookie)  # events happen while nobody is listening
        replay = read_frames(self.subscribe(cookie, last_event_id=0), until={"analysis.completed"})
        self.assertIn("analysis.completed", [f["event"] for f in replay])

    # ----- multiple tabs and isolation -----
    def test_two_tabs_of_one_user_both_receive_events(self):
        cookie = self.sign_up()
        tab_a, tab_b = self.subscribe(cookie), self.subscribe(cookie)
        self.upload(cookie)
        a = read_frames(tab_a, until={"analysis.completed"})
        b = read_frames(tab_b, until={"analysis.completed"})
        tab_a.close()
        tab_b.close()
        self.assertEqual([f["event"] for f in a], [f["event"] for f in b])
        self.assertEqual(a[-1]["event"], "analysis.completed")

    def test_other_users_never_receive_these_events(self):
        owner, other = self.sign_up(), self.sign_up()
        resp = self.subscribe(other)
        _, _, body = self.upload(owner, name="private-repo")
        aid = json.loads(body)["id"]
        frames = read_frames(resp, limit_seconds=2)
        resp.close()
        self.assertEqual(frames, [])
        other_replay = read_frames(self.subscribe(other, last_event_id=0), limit_seconds=2)
        self.assertNotIn(aid, json.dumps([f["data"] for f in other_replay]))
        owner_replay = read_frames(self.subscribe(owner, last_event_id=0), until={"analysis.completed"})
        self.assertIn(aid, json.dumps([f["data"] for f in owner_replay]))

    def test_events_carry_no_excerpts_evidence_or_secret_values(self):
        cookie = self.sign_up()
        self.upload(cookie)
        frames = read_frames(self.subscribe(cookie, last_event_id=0), until={"analysis.completed"})
        blob = json.dumps([f["data"] for f in frames])
        for forbidden in ('"excerpt"', '"evidence"', "AKIA", "password =", "SELECT "):
            self.assertNotIn(forbidden, blob)

    # ----- cleanup -----
    def test_closed_streams_release_their_server_thread(self):
        cookie = self.sign_up()
        before = threading.active_count()
        streams = [self.subscribe(cookie) for _ in range(3)]
        time.sleep(0.5)
        for s in streams:
            s.close()
        self.upload(cookie)  # wakes the handlers; a write to a closed socket ends each one
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and threading.active_count() > before:
            time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), before)


if __name__ == "__main__":
    unittest.main()
