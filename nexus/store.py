"""SQLite persistence (standard library). Schema is versioned; migrations run on open."""
import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .ratelimit import Limiter

SCHEMA_VERSION = 6
FINDING_STATUSES = ("open", "confirmed", "false_positive", "suppressed", "fixed")
REASON_REQUIRED = {"false_positive", "suppressed"}


log = logging.getLogger("nexus.store")
INTERRUPTED_MESSAGE = "The server stopped before this analysis finished. Start it again to rerun it."


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def camel(row):
    """Convert a database row to a dict with camelCase keys (matches the findings API)."""
    return {re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k): v for k, v in dict(row).items()}


class StoreError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


MIGRATIONS = {
    1: """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE analyses (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        error_message TEXT,
        partial INTEGER NOT NULL DEFAULT 0,
        partial_reason TEXT,
        files_scanned INTEGER NOT NULL DEFAULT 0,
        languages_json TEXT NOT NULL DEFAULT '{}',
        finding_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE findings (
        analysis_id TEXT NOT NULL REFERENCES analyses(id),
        id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL,
        confidence REAL NOT NULL,
        status TEXT NOT NULL,
        file_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        evidence TEXT NOT NULL,
        explanation TEXT NOT NULL,
        impact TEXT NOT NULL,
        remediation TEXT NOT NULL,
        verification_json TEXT NOT NULL,
        related_json TEXT NOT NULL,
        detection_method TEXT NOT NULL,
        risk_score REAL NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (analysis_id, id)
    );
    CREATE TABLE finding_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id TEXT NOT NULL,
        finding_id TEXT NOT NULL,
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_findings_analysis ON findings(analysis_id, severity, category);
    """,
    2: """
    ALTER TABLE findings ADD COLUMN excerpt_json TEXT NOT NULL DEFAULT '[]';
    CREATE TABLE analysis_files (
        analysis_id TEXT NOT NULL REFERENCES analyses(id),
        path TEXT NOT NULL,
        language TEXT NOT NULL,
        size INTEGER NOT NULL,
        lines INTEGER NOT NULL,
        PRIMARY KEY (analysis_id, path)
    );
    """,
    3: """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    ALTER TABLE projects ADD COLUMN owner_id TEXT REFERENCES users(id);
    CREATE INDEX idx_projects_owner ON projects(owner_id);
    """,
    4: """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id),
        type TEXT NOT NULL,
        project_id TEXT,
        analysis_id TEXT,
        data_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_events_user ON events(user_id, id);
    """,
    5: """
    ALTER TABLE findings ADD COLUMN evidence_class TEXT NOT NULL DEFAULT 'potential';
    ALTER TABLE analyses ADD COLUMN coverage_json TEXT NOT NULL DEFAULT '{}';
    """,
    6: """
    CREATE TABLE analysis_edges (
        analysis_id TEXT NOT NULL REFERENCES analyses(id),
        source_path TEXT NOT NULL,
        target_path TEXT NOT NULL,
        line INTEGER NOT NULL,
        statement TEXT NOT NULL,
        kind TEXT NOT NULL,
        PRIMARY KEY (analysis_id, source_path, target_path, line)
    );
    """,
}
EVENT_RETENTION = 2000  # events kept per user; older rows are pruned on write


class Store:
    def __init__(self, path=":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self.limiter = Limiter()
        self._migrate()
        self._ensure_ai_tables()
        self.recover_interrupted()

    def _ensure_ai_tables(self):
        with self._lock:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_explanations (
                    analysis_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (analysis_id, finding_id)
                );
                CREATE TABLE IF NOT EXISTS ai_group_explanations (
                    analysis_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (analysis_id, group_id)
                );
            """)

    def _migrate(self):
        """Each migration and its version bump run in one transaction, so a failure leaves the schema unchanged."""
        with self._lock:
            current = self._db.execute("PRAGMA user_version").fetchone()[0]
            for version in sorted(MIGRATIONS):
                if version > current:
                    self._db.commit()
                    script = "BEGIN;\n" + MIGRATIONS[version] + f"\nPRAGMA user_version = {int(version)};\nCOMMIT;"
                    try:
                        self._db.executescript(script)
                    except sqlite3.Error:
                        if self._db.in_transaction:
                            self._db.execute("ROLLBACK")
                        log.error("migration %s failed; schema left at version %s", version, current)
                        raise
                    current = version

    @contextmanager
    def _tx(self):
        """Group several statements into one commit. Any error rolls the whole group back."""
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def recover_interrupted(self):
        """Analyses that were queued or running when the process stopped can never finish. Mark them failed so the
        dashboard does not show them as active forever and the per-account concurrency cap is freed."""
        with self._tx() as db:
            cur = db.execute(
                "UPDATE analyses SET status = 'failed', stage = 'failed', error_code = 'interrupted', "
                "error_message = ?, updated_at = ? WHERE status IN ('queued', 'running')",
                (INTERRUPTED_MESSAGE, now_iso()))
            count = cur.rowcount
        if count:
            log.warning("marked %s interrupted analysis(es) as failed", count)
        return count

    def close(self):
        with self._lock:
            self._db.close()

    # ----- projects & analyses -----
    def create_project(self, name, source, owner_id=None):
        pid = "prj_" + uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute("INSERT INTO projects (id, name, source, created_at, owner_id) VALUES (?,?,?,?,?)",
                             (pid, name, source, now_iso(), owner_id))
            self._db.commit()
        return pid

    def list_projects(self, owner_id=None):
        sql = """SELECT p.*, (SELECT COUNT(*) FROM analyses a WHERE a.project_id = p.id) AS analysis_count,
                        (SELECT MAX(created_at) FROM analyses a WHERE a.project_id = p.id) AS last_analysis
                 FROM projects p"""
        args = ()
        if owner_id is not None:
            sql += " WHERE p.owner_id = ?"
            args = (owner_id,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY p.created_at DESC", args).fetchall()
        return [camel(r) for r in rows]

    # ----- users & sessions -----
    def create_user(self, email, name, password_hash):
        uid = "usr_" + uuid.uuid4().hex[:12]
        with self._lock:
            try:
                self._db.execute("INSERT INTO users VALUES (?,?,?,?,?)", (uid, email, name, password_hash, now_iso()))
            except sqlite3.IntegrityError:
                raise StoreError("email_taken", "An account with this email already exists.", 409)
            self._db.commit()
        return uid

    def get_user_by_email(self, email):
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def get_user(self, uid):
        with self._lock:
            row = self._db.execute("SELECT id, email, name, created_at FROM users WHERE id = ?", (uid,)).fetchone()
        return camel(row) if row else None

    def create_session(self, token_hash, user_id, expires_at):
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash, user_id, now_iso(), expires_at))

    def session_user(self, token_hash, now):
        with self._lock:
            row = self._db.execute(
                """SELECT u.id, u.email, u.name, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""", (token_hash, now)).fetchone()
        return camel(row) if row else None

    def delete_session(self, token_hash):
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self._db.commit()

    def count_active_analyses(self, owner_id=None):
        """Queued or running analyses for one account, or across all accounts when owner_id is None."""
        sql = "SELECT COUNT(*) FROM analyses a JOIN projects p ON p.id = a.project_id WHERE a.status IN ('queued', 'running')"
        args = ()
        if owner_id is not None:
            sql += " AND p.owner_id = ?"
            args = (owner_id,)
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return row[0]

    def analysis_context(self, aid):
        """Return (owner_id, project_id) for an analysis, or (None, None) if it does not exist."""
        with self._lock:
            row = self._db.execute(
                "SELECT p.owner_id, p.id FROM analyses a JOIN projects p ON p.id = a.project_id WHERE a.id = ?",
                (aid,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    # ----- events (persisted before they are published) -----
    def add_event(self, user_id, type_, project_id, analysis_id, data):
        with self._tx() as db:
            cur = db.execute(
                "INSERT INTO events (user_id, type, project_id, analysis_id, data_json, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, type_, project_id, analysis_id, json.dumps(data, sort_keys=True), now_iso()))
            eid = cur.lastrowid
            db.execute("DELETE FROM events WHERE user_id = ? AND id <= ?", (user_id, eid - EVENT_RETENTION))
        return eid

    def events_after(self, user_id, after_id, limit=200):
        with self._lock:
            rows = self._db.execute(
                "SELECT id, type, project_id, analysis_id, data_json, created_at FROM events "
                "WHERE user_id = ? AND id > ? ORDER BY id LIMIT ?", (user_id, after_id, limit)).fetchall()
        return [{"id": r[0], "type": r[1], "projectId": r[2], "analysisId": r[3],
                 "data": json.loads(r[4]), "createdAt": r[5]} for r in rows]

    def latest_event_id(self, user_id):
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE user_id = ?", (user_id,)).fetchone()
        return row[0]

    def analysis_owner(self, aid):
        with self._lock:
            row = self._db.execute(
                "SELECT p.owner_id FROM analyses a JOIN projects p ON p.id = a.project_id WHERE a.id = ?", (aid,)).fetchone()
        if not row:
            raise StoreError("not_found", "Analysis not found.", 404)
        return row[0]

    def project_owner(self, pid):
        with self._lock:
            row = self._db.execute("SELECT owner_id FROM projects WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise StoreError("not_found", "Project not found.", 404)
        return row[0]

    def rename_project(self, pid, name):
        with self._lock:
            self._db.execute("UPDATE projects SET name = ? WHERE id = ?", (name, pid))
            self._db.commit()

    def get_project(self, pid):
        with self._lock:
            row = self._db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise StoreError("not_found", "Project not found.", 404)
        return camel(row)

    def create_analysis(self, project_id):
        aid = "ana_" + uuid.uuid4().hex[:12]
        ts = now_iso()
        with self._lock:
            self._db.execute(
                "INSERT INTO analyses (id, project_id, status, stage, progress, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (aid, project_id, "queued", "queued", 0, ts, ts))
            self._db.commit()
        return aid

    def update_analysis(self, aid, **fields):
        if "languages" in fields:
            fields["languages_json"] = json.dumps(fields.pop("languages"), sort_keys=True)
        if "coverage" in fields:
            fields["coverage_json"] = json.dumps(fields.pop("coverage"), sort_keys=True)
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE analyses SET {cols} WHERE id = ?", (*fields.values(), aid))
            self._db.commit()

    def get_analysis(self, aid):
        with self._lock:
            row = self._db.execute(
                """SELECT a.*, p.name AS project_name, p.source AS project_source
                   FROM analyses a JOIN projects p ON p.id = a.project_id WHERE a.id = ?""", (aid,)).fetchone()
        if not row:
            raise StoreError("not_found", "Analysis not found.", 404)
        d = camel(row)
        d["languages"] = json.loads(d.pop("languagesJson"))
        d["coverage"] = json.loads(d.pop("coverageJson", "{}") or "{}")
        d["partial"] = bool(d["partial"])
        return d

    def list_analyses(self, project_id=None, owner_id=None):
        sql = "SELECT a.id FROM analyses a JOIN projects p ON p.id = a.project_id WHERE 1=1"
        args = ()
        if project_id:
            sql += " AND a.project_id = ?"
            args = (project_id,)
        if owner_id is not None:
            sql += " AND p.owner_id = ?"
            args = args + (owner_id,)
        with self._lock:
            ids = [r[0] for r in self._db.execute(sql + " ORDER BY a.created_at DESC", args).fetchall()]
        return [self.get_analysis(i) for i in ids]

    def save_files(self, aid, files):
        with self._tx() as db:
            db.executemany(
                "INSERT OR REPLACE INTO analysis_files (analysis_id, path, language, size, lines) VALUES (?,?,?,?,?)",
                [(aid, f["path"], f["language"], f["size"], f["lines"]) for f in files])

    def save_edges(self, aid, edges):
        with self._tx() as db:
            db.executemany(
                "INSERT OR REPLACE INTO analysis_edges (analysis_id, source_path, target_path, line, statement, kind) "
                "VALUES (?,?,?,?,?,?)",
                [(aid, e["source"], e["target"], e["line"], e["statement"], e["kind"]) for e in edges])

    def list_edges(self, aid):
        with self._lock:
            rows = self._db.execute(
                "SELECT source_path, target_path, line, statement, kind FROM analysis_edges "
                "WHERE analysis_id = ? ORDER BY source_path, target_path, line", (aid,)).fetchall()
        return [{"source": r[0], "target": r[1], "line": r[2], "statement": r[3], "kind": r[4]} for r in rows]

    def list_files(self, aid):
        self.get_analysis(aid)
        with self._lock:
            rows = self._db.execute(
                "SELECT path, language, size, lines FROM analysis_files WHERE analysis_id = ? ORDER BY path", (aid,)).fetchall()
        return [dict(r) for r in rows]

    # ----- findings -----
    def save_findings(self, aid, findings):
        with self._tx() as db:
            for f in findings:
                db.execute(
                    """INSERT INTO findings (analysis_id, id, rule_id, title, category, severity, confidence, status,
                       file_path, start_line, end_line, evidence, explanation, impact, remediation, verification_json,
                       related_json, detection_method, risk_score, created_at, excerpt_json, evidence_class)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (aid, f["id"], f["ruleId"], f["title"], f["category"], f["severity"], f["confidence"],
                     f["status"], f["filePath"], f["startLine"], f["endLine"], f["evidence"], f["explanation"],
                     f["impact"], f["remediation"], json.dumps(f["verificationSteps"]),
                     json.dumps(f["relatedFindings"]), f["detectionMethod"], f["riskScore"], f["createdAt"],
                     json.dumps(f.get("excerpt", [])), f.get("evidenceClass", "potential")))
            db.execute("UPDATE analyses SET finding_count = ? WHERE id = ?", (len(findings), aid))

    def list_findings(self, aid, category=None, severity=None, status=None, min_confidence=None, q=None,
                      sort="risk"):
        self.get_analysis(aid)
        clauses, args = ["analysis_id = ?"], [aid]
        if category:
            clauses.append("category = ?"); args.append(category)
        if severity:
            clauses.append("severity = ?"); args.append(severity)
        if status:
            clauses.append("status = ?"); args.append(status)
        if min_confidence is not None:
            clauses.append("confidence >= ?"); args.append(min_confidence)
        if q:
            clauses.append("(title LIKE ? OR file_path LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
        order = {"risk": "risk_score DESC, file_path, start_line",
                 "severity": "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, risk_score DESC",
                 "file": "file_path, start_line"}.get(sort, "risk_score DESC")
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM findings WHERE {' AND '.join(clauses)} ORDER BY {order}", args).fetchall()
        return [self._finding_row(r) for r in rows]

    def get_finding(self, aid, fid):
        with self._lock:
            row = self._db.execute("SELECT * FROM findings WHERE analysis_id = ? AND id = ?", (aid, fid)).fetchone()
            if not row:
                raise StoreError("not_found", "Finding not found in this analysis.", 404)
            events = self._db.execute(
                "SELECT from_status, to_status, reason, created_at FROM finding_events WHERE analysis_id = ? AND finding_id = ? ORDER BY id",
                (aid, fid)).fetchall()
        f = self._finding_row(row)
        f["history"] = [dict(e) for e in events]
        return f

    def finding_summaries(self, aid, ids):
        out = []
        with self._lock:
            for fid in ids:
                row = self._db.execute(
                    "SELECT id, title, severity, category, file_path, start_line, status FROM findings WHERE analysis_id = ? AND id = ?",
                    (aid, fid)).fetchone()
                if row:
                    out.append({"id": row["id"], "title": row["title"], "severity": row["severity"],
                                "category": row["category"], "filePath": row["file_path"],
                                "startLine": row["start_line"], "status": row["status"]})
        return out

    def set_finding_status(self, aid, fid, status, reason=None):
        if status not in FINDING_STATUSES:
            raise StoreError("invalid_status", f"Status must be one of: {', '.join(FINDING_STATUSES)}.")
        if status in REASON_REQUIRED and not (reason and reason.strip()):
            raise StoreError("reason_required", "A reason is required for this status change.")
        current = self.get_finding(aid, fid)
        with self._tx() as db:
            db.execute("UPDATE findings SET status = ? WHERE analysis_id = ? AND id = ?", (status, aid, fid))
            db.execute(
                "INSERT INTO finding_events (analysis_id, finding_id, from_status, to_status, reason, created_at) VALUES (?,?,?,?,?,?)",
                (aid, fid, current["status"], status, (reason or "").strip() or None, now_iso()))
        self.invalidate_cached_ai_explanation(aid, fid)
        return self.get_finding(aid, fid)

    def get_cached_ai_explanation(self, aid, fid):
        with self._lock:
            row = self._db.execute(
                "SELECT result_json FROM ai_explanations WHERE analysis_id = ? AND finding_id = ?",
                (aid, fid)).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return None
            return None

    def save_cached_ai_explanation(self, aid, fid, provider, model, result):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO ai_explanations (analysis_id, finding_id, provider, model, result_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (aid, fid, provider, model, json.dumps(result, sort_keys=True), now_iso()))
            self._db.commit()

    def invalidate_cached_ai_explanation(self, aid, fid):
        with self._lock:
            self._db.execute(
                "DELETE FROM ai_explanations WHERE analysis_id = ? AND finding_id = ?",
                (aid, fid))
            self._db.commit()

    def get_cached_group_explanation(self, aid, gid):
        with self._lock:
            row = self._db.execute(
                "SELECT result_json FROM ai_group_explanations WHERE analysis_id = ? AND group_id = ?",
                (aid, gid)).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return None
            return None

    def save_cached_group_explanation(self, aid, gid, provider, model, result):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO ai_group_explanations (analysis_id, group_id, provider, model, result_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (aid, gid, provider, model, json.dumps(result, sort_keys=True), now_iso()))
            self._db.commit()

    @staticmethod
    def _finding_row(row):
        d = dict(row)
        d["verificationSteps"] = json.loads(d.pop("verification_json"))
        d["relatedFindings"] = json.loads(d.pop("related_json"))
        d["excerpt"] = json.loads(d.pop("excerpt_json", "[]") or "[]")
        keys = {"analysis_id": "analysisId", "rule_id": "ruleId", "file_path": "filePath", "start_line": "startLine",
                "end_line": "endLine", "detection_method": "detectionMethod", "risk_score": "riskScore",
                "created_at": "createdAt", "evidence_class": "evidenceClass"}
        out = {}
        for k, v in d.items():
            out[keys.get(k, k)] = v
        return out
