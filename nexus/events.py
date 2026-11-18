"""Live update events. Every event is written to the database first (so a reconnecting client can replay
from its last event id), then listeners are woken. Payloads are built from an explicit whitelist below, so
excerpts, evidence, secrets and source code never reach the event stream."""
import logging
import threading

log = logging.getLogger("nexus.events")

EVENT_TYPES = (
    "project.created", "analysis.queued", "analysis.started", "analysis.stage_updated",
    "analysis.completed", "analysis.failed", "finding.created", "finding.updated",
    "finding.resolved", "report.generated",
)
MAX_FINDING_EVENTS = 500  # per analysis; beyond this the completed event carries the totals only


class Broker:
    """Wakes waiting stream handlers when a new event has been committed. Carries no event data itself."""

    def __init__(self):
        self._cond = threading.Condition()
        self._generation = 0

    def notify(self):
        with self._cond:
            self._generation += 1
            self._cond.notify_all()

    def wait(self, timeout):
        with self._cond:
            self._cond.wait(timeout)


BROKER = Broker()


def publish(store, user_id, type_, project_id=None, analysis_id=None, data=None):
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type_}")
    eid = store.add_event(user_id, type_, project_id, analysis_id, data or {})
    BROKER.notify()
    return eid


def publish_analysis(store, analysis_id, type_, **data):
    """Publish for the owner of an analysis. Analyses without an owner (legacy rows) publish nothing."""
    owner, project_id = store.analysis_context(analysis_id)
    if owner is None:
        return None
    return publish(store, owner, type_, project_id, analysis_id, data)


def finding_payload(f):
    return {"id": f["id"], "ruleId": f["ruleId"], "title": f["title"], "severity": f["severity"],
            "category": f["category"], "filePath": f["filePath"], "startLine": f["startLine"],
            "status": f.get("status", "open")}
