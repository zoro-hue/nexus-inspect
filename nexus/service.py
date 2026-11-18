"""Analysis orchestration: validate, ingest, scan, persist.

Every analysis is split into start (creates the project and a queued analysis record, validating input first)
and execute (runs the pipeline and writes each stage to the store as it happens). The API can run execute
inline, or on a background thread so the frontend can poll live progress and request cancellation.
"""
import logging
import tempfile
import threading

from . import events
from . import rules as R, ingest
from .scanner import scan
from .store import Store, now_iso

log = logging.getLogger("nexus")
_cancel_lock = threading.Lock()
_cancel_flags = {}


class Cancelled(Exception):
    """Raised at a stage boundary when the user has asked to stop an analysis."""


def request_cancel(aid: str) -> bool:
    with _cancel_lock:
        _cancel_flags[aid] = True
    return True


def _check_cancel(aid):
    with _cancel_lock:
        flagged = _cancel_flags.pop(aid, False)
    if flagged:
        raise Cancelled()


def _publish(store, aid, type_, **data):
    """Event publication must never change the outcome of an analysis, so failures are logged and ignored."""
    try:
        events.publish_analysis(store, aid, type_, **data)
    except Exception:  # noqa: BLE001
        log.exception("could not publish %s for analysis %s", type_, aid)


def _stage_writer(store, aid):
    def on_stage(stage, pct):
        _check_cancel(aid)
        store.update_analysis(aid, stage=stage, progress=pct)
        _publish(store, aid, "analysis.stage_updated", stage=stage)
    return on_stage


def start_archive(store: Store, project_name: str, source: str, owner_id=None) -> str:
    project_id = store.create_project(project_name, source, owner_id)
    aid = store.create_analysis(project_id)
    _publish(store, aid, "project.created", projectName=project_name)
    _publish(store, aid, "analysis.queued", projectName=project_name)
    return aid


def start_url(store: Store, url: str, branch: str, owner_id=None) -> str:
    """Validate the URL and branch before creating any record, then create the queued analysis."""
    owner, repo = ingest.parse_github_url(url)
    branch = ingest.validate_branch(branch)
    name = f"{owner}/{repo}"
    project_id = store.create_project(name, f"github.com/{owner}/{repo}@{branch}", owner_id)
    aid = store.create_analysis(project_id)
    _publish(store, aid, "project.created", projectName=name)
    _publish(store, aid, "analysis.queued", projectName=name)
    return aid


def coverage_of(result):
    """What the analysis looked at and what it skipped, stored with the analysis so reports can state it."""
    return {
        "filesScanned": result.files_scanned,
        "languages": result.languages,
        "skipped": dict(sorted(result.files_skipped.items())),
        "parseFailures": list(result.parse_failures),
        "deduplicated": result.deduplicated,
        "ruleCount": len(R.RULES),
        "partial": bool(result.partial),
        "partialReason": result.partial_reason,
    }


def _finish(store, aid, result):
    store.save_findings(aid, result.findings)
    store.save_files(aid, result.file_index)
    store.save_edges(aid, result.edges)
    store.update_analysis(aid, status="completed", stage="report_generation", progress=100,
                          files_scanned=result.files_scanned, languages=result.languages,
                          partial=int(result.partial), partial_reason=result.partial_reason,
                          coverage=coverage_of(result))
    # Findings are written in one transaction, so they are announced together after they are persisted.
    for f in result.findings[:events.MAX_FINDING_EVENTS]:
        _publish(store, aid, "finding.created", **events.finding_payload(f))
    counts = {}
    for f in result.findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    _publish(store, aid, "analysis.completed", findingCount=len(result.findings), bySeverity=counts,
             filesScanned=result.files_scanned, partial=bool(result.partial))


def _fail(store, aid, exc):
    if isinstance(exc, Cancelled):
        store.update_analysis(aid, status="cancelled", stage="cancelled", error_code="cancelled",
                              error_message="The analysis was cancelled before it finished.")
        _publish(store, aid, "analysis.failed", status="cancelled", errorCode="cancelled",
                 errorMessage="The analysis was cancelled before it finished.")
    elif isinstance(exc, ingest.IngestError):
        store.update_analysis(aid, status="failed", stage="failed", error_code=exc.code, error_message=exc.message)
        _publish(store, aid, "analysis.failed", status="failed", errorCode=exc.code, errorMessage=exc.message)
    else:
        log.exception("analysis %s failed", aid)
        store.update_analysis(aid, status="failed", stage="failed", error_code="internal_error",
                              error_message="The analysis failed unexpectedly. Check the server log.")
        _publish(store, aid, "analysis.failed", status="failed", errorCode="internal_error",
                 errorMessage="The analysis failed unexpectedly. Check the server log.")


def execute_archive(store: Store, aid: str, archive: bytes, time_budget=60.0):
    try:
        store.update_analysis(aid, status="running", stage="ingestion", progress=15)
        _publish(store, aid, "analysis.started")
        _check_cancel(aid)
        with tempfile.TemporaryDirectory(prefix="nexus-") as work:
            root = ingest.extract_zip(archive, work)
            result = scan(root, now_iso(), time_budget=time_budget, on_stage=_stage_writer(store, aid))
        _finish(store, aid, result)
    except Exception as exc:  # noqa: BLE001 - recorded; internals never leave the server
        _fail(store, aid, exc)
    return store.get_analysis(aid)


def execute_url(store: Store, aid: str, url: str, branch: str, opener=None, time_budget=60.0):
    try:
        store.update_analysis(aid, status="running", stage="ingestion", progress=10)
        _publish(store, aid, "analysis.started")
        _check_cancel(aid)
        with tempfile.TemporaryDirectory(prefix="nexus-") as work:
            root = ingest.fetch_repository_source(url, branch, work, opener=opener)
            result = scan(root, now_iso(), time_budget=time_budget, on_stage=_stage_writer(store, aid))
        _finish(store, aid, result)
    except Exception as exc:  # noqa: BLE001
        _fail(store, aid, exc)
    return store.get_analysis(aid)


def run_archive_analysis(store: Store, project_name, source, archive, time_budget=60.0, owner_id=None):
    aid = start_archive(store, project_name, source, owner_id)
    return execute_archive(store, aid, archive, time_budget)


def run_url_analysis(store: Store, url, branch, opener=None, time_budget=60.0, owner_id=None):
    aid = start_url(store, url, branch, owner_id)
    return execute_url(store, aid, url, branch, opener, time_budget)


def launch(target, *args, **kwargs):
    """Run an execute_* function on a daemon thread; the caller polls the store for progress."""
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True, name="nexus-analysis")
    thread.start()
    return thread
