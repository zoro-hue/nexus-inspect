"""HTTP API routing. Pure functions over (method, path, query, body) so they can be tested without sockets."""
import json
import logging
import os
import re
from urllib.parse import parse_qs, urlsplit

from . import ai
from . import assistant
from . import investigation
from . import report as REPORT
from . import graph as GRAPH
from . import auth
from . import events
from .ratelimit import RateLimited
from . import ingest
from . import rules as R
from . import __version__, service
from .ingest import IngestError
from .rules import RULES_BY_ID
from .store import FINDING_STATUSES, StoreError, Store

MAX_JSON_BODY = 64 * 1024
MAX_ZIP_BODY = 25 * 1024 * 1024
REPORT_FORMATS = ("json", "markdown", "sarif")


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _json(status, payload):
    return status, {"Content-Type": "application/json; charset=utf-8"}, json.dumps(payload, indent=2).encode()


def _error(status, code, message):
    return _json(status, {"error": {"code": code, "message": message}})


def _parse_json_body(body: bytes):
    if len(body) > MAX_JSON_BODY:
        raise ApiError(413, "body_too_large", "The request body is larger than 64 KB.")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise ApiError(400, "invalid_json", "The request body must be valid JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "The request body must be a JSON object.")
    return data


def _opt_float(query, key):
    if key not in query:
        return None
    try:
        v = float(query[key][0])
    except ValueError:
        raise ApiError(400, "invalid_parameter", f"{key} must be a number between 0 and 1.")
    if not 0 <= v <= 1:
        raise ApiError(400, "invalid_parameter", f"{key} must be between 0 and 1.")
    return v


log = logging.getLogger("nexus.api")
MAX_ACTIVE_ANALYSES = 3   # per account; further starts are refused until one finishes
DEFAULT_MAX_ACTIVE_TOTAL = 8  # across all accounts; keeps one process from running unbounded analysis threads
CREATE_LIMIT = (40, 3600)  # analysis starts per account per hour
INSPECT_LIMIT = (120, 3600)
SIGNUP_LIMIT = (10, 3600)  # per client address
SIGNIN_LIMIT = (30, 900)   # per client address, on top of the per-email throttle in auth.py


def _rate(store, key, limit, window):
    """Raises RateLimited, which handle() turns into a 429 with a Retry-After header."""
    store.limiter.check(key, limit, window)


def _assert_owner(store, user, aid):
    """Other users' analyses are reported as missing, so their existence is not disclosed."""
    if store.analysis_owner(aid) != user["id"]:
        raise StoreError("not_found", "Analysis not found.", 404)


def _session_response(store, uid, status):
    token, digest = auth.new_token()
    store.create_session(digest, uid, auth.expiry_iso())
    code, headers, body = _json(status, {"user": store.get_user(uid)})
    headers["Set-Cookie"] = auth.set_cookie_header(token)
    return code, headers, body


def _auth_route(store, method, path, body, user, headers, client_ip=None):
    if path == "/api/auth/me" and method == "GET":
        # Signed out is a normal state, not an error, so this returns 200 with user null (no console 401 noise).
        return _json(200, {"user": user})
    if path == "/api/auth/signup" and method == "POST":
        _rate(store, f"signup:{client_ip or 'unknown'}", *SIGNUP_LIMIT)
        data = _parse_json_body(body)
        email, name = auth.validate_signup(data.get("email"), data.get("name"), data.get("password"))
        uid = store.create_user(email, name, auth.hash_password(data["password"]))
        return _session_response(store, uid, 201)
    if path == "/api/auth/signin" and method == "POST":
        _rate(store, f"signin:{client_ip or 'unknown'}", *SIGNIN_LIMIT)
        data = _parse_json_body(body)
        email = auth.validate_signin(data.get("email"), data.get("password"))
        auth.check_throttle(email)
        record = store.get_user_by_email(email)
        if record is None:
            auth.burn_dummy_hash(data.get("password"))
            auth.record_failure(email)
            raise auth.AuthError(401, "invalid_credentials", "Email or password is incorrect.")
        if not auth.verify_password(data["password"], record["password_hash"]):
            auth.record_failure(email)
            raise auth.AuthError(401, "invalid_credentials", "Email or password is incorrect.")
        auth.clear_failures(email)
        return _session_response(store, record["id"], 200)
    if path == "/api/auth/signout" and method == "POST":
        token = auth.token_from_headers(headers)
        if token:
            store.delete_session(auth.hash_token(token))
        code, hdrs, payload = _json(200, {"signedOut": True})
        hdrs["Set-Cookie"] = auth.clear_cookie_header()
        return code, hdrs, payload
    raise ApiError(404, "not_found", "No such endpoint.")


def _one(query, key):
    return query.get(key, [None])[0]


def _investigation_payload(store, aid):
    analysis = store.get_analysis(aid)
    findings = store.list_findings(aid)
    return {"analysisId": aid, "findingCount": len(findings), "ai": ai.status(),
            "investigation": investigation.build(findings, analysis.get("coverage") or {})}


def summarize(findings):
    by_sev = {s: 0 for s in ("critical", "high", "medium", "low")}
    by_cat = {c: 0 for c in ("security", "slop", "gap", "loose")}
    for f in findings:
        by_sev[f["severity"]] += 1
        by_cat[f["category"]] += 1
    return {"total": len(findings), "bySeverity": by_sev, "byCategory": by_cat}


def _graph_options(query):
    """Validate graph filters from the query string. Unknown values are refused rather than ignored."""
    options = {}
    category = _one(query, "category")
    if category:
        if category not in REPORT.CATEGORIES:
            raise ApiError(400, "invalid_parameter", f"category must be one of: {', '.join(REPORT.CATEGORIES)}.")
        options["category"] = category
    severity = _one(query, "severity")
    if severity:
        if severity not in REPORT.SEVERITIES:
            raise ApiError(400, "invalid_parameter", f"severity must be one of: {', '.join(REPORT.SEVERITIES)}.")
        options["severity"] = severity
    evidence = _one(query, "evidenceClass")
    if evidence:
        if evidence not in investigation.EVIDENCE_CLASSES:
            raise ApiError(400, "invalid_parameter", "evidenceClass must be confirmed, potential or informational.")
        options["evidenceClass"] = evidence
    raw_risk = _one(query, "minRisk")
    if raw_risk is not None:
        try:
            value = float(raw_risk)
        except ValueError:
            raise ApiError(400, "invalid_parameter", "minRisk must be a number from 0 to 100.")
        if not 0 <= value <= 100:
            raise ApiError(400, "invalid_parameter", "minRisk must be a number from 0 to 100.")
        options["minRisk"] = value
    for key, low, high in (("limit", 10, GRAPH.MAX_NODES_LIMIT), ("edgeLimit", 10, GRAPH.MAX_EDGES_LIMIT)):
        raw = _one(query, key)
        if raw is not None:
            if not raw.isdigit() or not low <= int(raw) <= high:
                raise ApiError(400, "invalid_parameter", f"{key} must be a whole number from {low} to {high}.")
            options[key] = int(raw)
    structure = _one(query, "structure")
    if structure is not None:
        if structure not in ("0", "1"):
            raise ApiError(400, "invalid_parameter", "structure must be 0 or 1.")
        options["structure"] = structure == "1"
    focus = _one(query, "focus")
    if focus:
        if not re.fullmatch(r"[A-Za-z0-9_:./\-]{1,300}", focus):
            raise ApiError(400, "invalid_parameter", "focus is not a valid node identifier.")
        options["focus"] = focus
    return options


def _asynchronous(query):
    return (_one(query, "async") or "").lower() in ("1", "true", "yes")


def current_user(store, headers):
    """The signed-in user for these request headers, or None. Shared by the JSON API and the event stream."""
    token = auth.token_from_headers(headers or {})
    return store.session_user(auth.hash_token(token), auth.now_iso()) if token else None


def max_active_total(env=None):
    env = os.environ if env is None else env
    try:
        return max(1, int(env.get("NEXUS_MAX_ACTIVE_ANALYSES", DEFAULT_MAX_ACTIVE_TOTAL)))
    except ValueError:
        return DEFAULT_MAX_ACTIVE_TOTAL


def _start_allowed(store, user):
    """Rate limit and concurrency caps for every request that starts expensive work."""
    _rate(store, f"create:{user['id']}", *CREATE_LIMIT)
    if store.count_active_analyses(user["id"]) >= MAX_ACTIVE_ANALYSES:
        raise ApiError(429, "too_many_active", f"You already have {MAX_ACTIVE_ANALYSES} analyses running. "
                                               "Wait for one to finish before starting another.")
    if store.count_active_analyses() >= max_active_total():
        raise ApiError(503, "server_busy", "The server is running its maximum number of analyses. "
                                           "Try again when one finishes.")


def handle(store: Store, method: str, raw_path: str, body: bytes = b"", headers=None, opener=None, client_ip=None):
    """Return (status, headers, body_bytes) for an API request."""
    headers = headers or {}
    parts = urlsplit(raw_path)
    path = parts.path.rstrip("/") or "/"
    query = parse_qs(parts.query)
    user = current_user(store, headers)
    try:
        if path == "/health" and method == "GET":
            return _json(200, {"ok": True, "service": "nexus-inspect", "version": __version__})
        if path.startswith("/api/auth/"):
            return _auth_route(store, method, path, body, user, headers, client_ip)
        PUBLIC_API_PATHS = (
            "/api/rules",
            "/api/reference",
            "/api/ai/chat",
            "/api/assistant/chat",
            "/api/ai/assistant/suggestions",
        )
        if path.startswith("/api/") and path not in PUBLIC_API_PATHS and user is None:
            raise ApiError(401, "unauthorized", "Sign in to continue.")
        if path in ("/api/ai/chat", "/api/assistant/chat") and method == "POST":
            data = _parse_json_body(body)
            msg = data.get("message", "")
            ctx = data.get("context", {})
            return _json(200, assistant.chat(store, user, msg, ctx, opener=opener))
        if path == "/api/ai/assistant/suggestions" and method == "GET":
            route = _one(query, "route") or ""
            aid = _one(query, "analysisId")
            has_analysis = False
            if aid and user:
                try:
                    has_analysis = (store.analysis_owner(aid) == user["id"])
                except Exception:
                    has_analysis = False
            return _json(200, {"suggestions": assistant.get_suggestions(route, has_analysis)})
        if path == "/api/projects" and method == "GET":
            return _json(200, {"items": store.list_projects(user["id"])})
        if path == "/api/analyses" and method == "GET":
            return _json(200, {"items": store.list_analyses(_one(query, "projectId"), user["id"])})
        if path == "/api/analyses" and method == "POST":
            ctype = headers.get("Content-Type", "")
            if not ctype.startswith("application/zip"):
                raise ApiError(415, "unsupported_media_type", "Upload a repository as application/zip.")
            if len(body) > MAX_ZIP_BODY:
                raise ApiError(413, "archive_too_large", "The archive is larger than the 25 MB limit.")
            name = (_one(query, "name") or "uploaded-repository")[:120]
            _start_allowed(store, user)
            if _asynchronous(query):
                aid = service.start_archive(store, name, f"upload:{name}", user["id"])
                service.launch(service.execute_archive, store, aid, body)
                return _json(202, store.get_analysis(aid))
            analysis = service.run_archive_analysis(store, name, f"upload:{name}", body, owner_id=user["id"])
            return _json(201 if analysis["status"] == "completed" else 422, analysis)
        if path == "/api/repositories/inspect" and method == "POST":
            _rate(store, f"inspect:{user['id']}", *INSPECT_LIMIT)
            data = _parse_json_body(body)
            return _json(200, ingest.inspect_repository(data.get("url", ""), opener=opener))
        if path == "/api/ai/status" and method == "GET":
            return _json(200, ai.status())
        m_project = re.fullmatch(r"/api/projects/([A-Za-z0-9_]+)", path)
        if m_project:
            pid = m_project.group(1)
            if store.project_owner(pid) != user["id"]:
                raise StoreError("not_found", "Project not found.", 404)
            if method == "GET":
                project = store.get_project(pid)
                project["analyses"] = store.list_analyses(pid, user["id"])
                return _json(200, project)
            if method == "PATCH":
                data = _parse_json_body(body)
                name = data.get("name")
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                    raise ApiError(422, "validation_failed", "name must be between 1 and 120 characters.")
                store.rename_project(pid, name.strip())
                return _json(200, store.get_project(pid))
        m_explain = re.fullmatch(r"/api/analyses/([A-Za-z0-9_]+)/findings/([A-Za-z0-9_-]+)/explain", path)
        if m_explain and method == "POST":
            aid, fid = m_explain.group(1), m_explain.group(2)
            _assert_owner(store, user, aid)
            finding = store.get_finding(aid, fid)
            refresh = _one(query, "refresh") == "1"
            fallback = _one(query, "fallback") == "1"
            if not refresh:
                cached = store.get_cached_ai_explanation(aid, fid)
                if cached:
                    cached["cached"] = True
                    return _json(200, cached)
            try:
                res = ai.explain(finding, opener=opener, fallback=fallback)
                store.save_cached_ai_explanation(aid, fid, res.get("provider", "gemini"), res.get("model", ""), res)
                return _json(200, res)
            except ai.AIError:
                if fallback:
                    return _json(200, ai.deterministic_explanation(finding))
                raise
        m_inv = re.fullmatch(r"/api/analyses/([A-Za-z0-9_]+)/investigation", path)
        if m_inv and method == "GET":
            _assert_owner(store, user, m_inv.group(1))
            return _json(200, _investigation_payload(store, m_inv.group(1)))
        m_grp = re.fullmatch(r"/api/analyses/([A-Za-z0-9_]+)/investigation/groups/([A-Za-z0-9_-]+)/explain", path)
        if m_grp and method == "POST":
            aid = m_grp.group(1)
            gid = m_grp.group(2)
            _assert_owner(store, user, aid)
            group = investigation.find_group(_investigation_payload(store, aid)["investigation"], gid)
            if group is None:
                raise ApiError(404, "not_found", "Root-cause group not found in this analysis.")
            refresh = _one(query, "refresh") == "1"
            if not refresh:
                cached = store.get_cached_group_explanation(aid, gid)
                if cached:
                    cached["cached"] = True
                    return _json(200, cached)
            findings = [store.get_finding(aid, fid) for fid in group["findingIds"][:ai.GROUP_MAX_FINDINGS]]
            res = ai.explain_group(group, findings, opener=opener)
            store.save_cached_group_explanation(aid, gid, res.get("provider", "gemini"), res.get("model", ""), res)
            return _json(200, res)
        m_graph = re.fullmatch(r"/api/analyses/([A-Za-z0-9_]+)/graph", path)
        if m_graph and method == "GET":
            aid = m_graph.group(1)
            _assert_owner(store, user, aid)
            options = _graph_options(query)
            findings = store.list_findings(aid)
            groups = investigation.build(findings, store.get_analysis(aid).get("coverage") or {})["rootCauseGroups"]
            return _json(200, GRAPH.build(findings, store.list_edges(aid), groups, options))
        if path == "/api/reference" and method == "GET":
            from .catalog import ENDPOINTS
            return _json(200, {"items": ENDPOINTS})
        if path == "/api/rules" and method == "GET":
            return _json(200, {"items": [
                {"id": r.id, "title": r.title, "category": r.category, "severity": r.severity,
                 "confidence": r.confidence, "impact": r.impact, "remediation": r.remediation,
                 "verification": list(r.verification), "method": r.method,
                 "evidenceClass": r.evidence_class} for r in R.RULES]})
        if path == "/api/analyses/url" and method == "POST":
            data = _parse_json_body(body)
            url, branch = data.get("url", ""), data.get("branch", "") or ""
            if not isinstance(url, str):
                raise ApiError(422, "invalid_url", "url must be a string.")
            if not isinstance(branch, str):
                raise ApiError(422, "invalid_branch", "branch must be a string.")
            _start_allowed(store, user)
            if _asynchronous(query):
                aid = service.start_url(store, url, branch, user["id"])
                service.launch(service.execute_url, store, aid, url, branch, opener)
                return _json(202, store.get_analysis(aid))
            analysis = service.run_url_analysis(store, url, branch, opener=opener, owner_id=user["id"])
            return _json(201 if analysis["status"] == "completed" else 422, analysis)

        m = re.fullmatch(r"/api/analyses/([A-Za-z0-9_]+)(/findings(?:/([A-Za-z0-9_-]+))?|/report|/files|/cancel|/diff)?", path)
        if m:
            aid, tail, fid = m.group(1), m.group(2), m.group(3)
            _assert_owner(store, user, aid)
            if tail == "/diff" and method == "GET":
                against = _one(query, "against")
                if not against:
                    raise ApiError(400, "missing_parameter", "against query parameter is required.")
                _assert_owner(store, user, against)
                current_f = store.list_findings(aid)
                baseline_f = store.list_findings(against)
                def _k(f):
                    return (f.get("ruleId"), f.get("filePath"), f.get("startLine"))
                baseline_map = {_k(f): f for f in baseline_f}
                current_map = {_k(f): f for f in current_f}
                new_items = [f for k, f in current_map.items() if k not in baseline_map]
                resolved_items = [f for k, f in baseline_map.items() if k not in current_map]
                persisting_items = [f for k, f in current_map.items() if k in baseline_map]
                return _json(200, {
                    "currentAnalysisId": aid,
                    "baselineAnalysisId": against,
                    "summary": {
                        "currentTotal": len(current_f),
                        "baselineTotal": len(baseline_f),
                        "delta": len(current_f) - len(baseline_f),
                        "newCount": len(new_items),
                        "resolvedCount": len(resolved_items),
                        "persistingCount": len(persisting_items),
                    },
                    "new": new_items,
                    "resolved": resolved_items,
                    "persisting": persisting_items,
                })
            if tail is None and method == "GET":
                return _json(200, store.get_analysis(aid))
            if tail == "/findings" and method == "GET":
                items = store.list_findings(
                    aid, category=_one(query, "category"), severity=_one(query, "severity"),
                    status=_one(query, "status"), min_confidence=_opt_float(query, "minConfidence"),
                    q=_one(query, "q"), sort=_one(query, "sort") or "risk")
                return _json(200, {"items": items, "summary": summarize(store.list_findings(aid))})
            if tail == "/files" and method == "GET":
                return _json(200, {"items": store.list_files(aid)})
            if tail == "/cancel" and method == "POST":
                current = store.get_analysis(aid)
                if current["status"] not in ("queued", "running"):
                    raise ApiError(409, "not_cancellable", "Only queued or running analyses can be cancelled.")
                service.request_cancel(aid)
                return _json(202, current)
            if tail and tail.startswith("/findings/") and method == "GET":
                finding = store.get_finding(aid, fid)
                finding["relatedDetails"] = store.finding_summaries(aid, finding["relatedFindings"])
                return _json(200, finding)
            if tail and tail.startswith("/findings/") and method == "PATCH":
                data = _parse_json_body(body)
                if data.get("status") not in FINDING_STATUSES:
                    raise ApiError(400, "invalid_status", f"status must be one of: {', '.join(FINDING_STATUSES)}.")
                reason = data.get("reason")
                if reason is not None and not isinstance(reason, str):
                    raise ApiError(400, "invalid_reason", "reason must be a string.")
                updated = store.set_finding_status(aid, fid, data["status"], reason)
                events.publish_analysis(store, aid, "finding.resolved" if data["status"] == "fixed" else "finding.updated",
                                        **events.finding_payload(updated))
                return _json(200, updated)
            if tail == "/report" and method == "GET":
                fmt = _one(query, "format") or "json"
                if fmt not in REPORT_FORMATS:
                    raise ApiError(400, "invalid_format", "format must be json, markdown or sarif.")
                analysis = store.get_analysis(aid)
                project = store.get_project(analysis["projectId"])
                report = REPORT.build(analysis, project, store.list_findings(aid), store.list_edges(aid))
                events.publish_analysis(store, aid, "report.generated", format=fmt)
                name = f"nexus-report-{aid}"
                if fmt == "markdown":
                    return (200, {"Content-Type": "text/markdown; charset=utf-8",
                                  "Content-Disposition": f'attachment; filename="{name}.md"'},
                            REPORT.to_markdown(report).encode("utf-8"))
                if fmt == "sarif":
                    code, hdrs, body = _json(200, REPORT.to_sarif(report))
                    hdrs["Content-Type"] = "application/sarif+json; charset=utf-8"
                    hdrs["Content-Disposition"] = f'attachment; filename="{name}.sarif"'
                    return code, hdrs, body
                code, hdrs, body = _json(200, report)
                hdrs["Content-Disposition"] = f'attachment; filename="{name}.json"'
                return code, hdrs, body
        raise ApiError(404, "not_found", "No such endpoint.")
    except ApiError as exc:
        return _error(exc.status, exc.code, exc.message)
    except RateLimited as exc:
        status, hdrs, payload = _error(429, "rate_limited", str(exc))
        hdrs["Retry-After"] = str(exc.retry_after)
        return status, hdrs, payload
    except auth.AuthError as exc:
        return _json(exc.status, {"error": {"code": exc.code, "message": exc.message, "fields": exc.fields}})
    except StoreError as exc:
        return _error(exc.status, exc.code, exc.message)
    except IngestError as exc:
        status, hdrs, payload = _error(exc.status, exc.code, exc.message)
        if getattr(exc, "retry_after", None):
            hdrs["Retry-After"] = str(exc.retry_after)
        return status, hdrs, payload
    except ai.AIError as exc:
        return _error(exc.status, exc.code, exc.message)
    except Exception:  # noqa: BLE001 - the details go to the server log only, never to the client
        log.exception("unhandled error for %s %s", method, path)
        return _error(500, "internal_error", "Something went wrong on the server. The details were logged.")
