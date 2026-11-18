# NEXUS INSPECT

NEXUS INSPECT investigates a code repository for security weaknesses, AI-generated implementation slop, coding gaps and loose ends. Every finding names a rule, a file, a line range and a redacted excerpt, with severity, confidence, remediation and verification steps.

The application is a Python 3 standard-library server (`http.server`, `sqlite3`, `zipfile`) with a static HTML, CSS and vanilla JavaScript frontend. It has no third-party Python packages.

## Quick start

```bash
python -m pip install -r requirements.txt   # installs nothing; stdlib only
python -m unittest discover -v              # runs the test suite (364 tests; 5 optional selenium tests skip when selenium is not installed)
python server.py                            # http://127.0.0.1:8000 (PORT and HOST can override)
```

Open `http://127.0.0.1:8000`, choose **Sign up**, then **Analyze**. Enter a public `https://github.com/owner/repo` URL and check it, or upload a ZIP archive of up to 25 MB.

The same commands are declared in `arena.json`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8000` | Listening port. |
| `HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` to keep the server local. |
| `NEXUS_DB` | `data/nexus.sqlite` | SQLite file, created on first start. |
| `NEXUS_COOKIE_SECURE` | unset | Set to `1` when serving over HTTPS so the session cookie is only sent on secure connections. |
| `NEXUS_AI_PROVIDER` | `gemini` | AI provider for explanations and assistant (`gemini`, `anthropic`, `openai`). |
| `NEXUS_AI_API_KEY` | unset | Enables live AI explanations and assistant. Without it, the deterministic rule engine provides full fallback explanations. |
| `NEXUS_AI_MODEL` | `gemini-2.0-flash` | Model name for AI explanations (default: `gemini-2.0-flash` for Gemini, `claude-3-5-haiku-20241022` for Anthropic, `gpt-4o-mini` for OpenAI). |
| `NEXUS_MAX_ACTIVE_ANALYSES` | `8` | Analyses running at once across all accounts. Further starts receive 503 `server_busy`. |
| `GITHUB_TOKEN` | unset | Optional GitHub personal access token for authenticated GitHub REST API requests (5,000 req/hr). When unset or exhausted, Git transport fallback is used. |

`.env.example` lists these variables with placeholder values. `server.py` does not read that file, so export the variables in your shell or process manager. Never commit a real key.

## Accounts and sessions

- Sign-up requires a valid email, a name of 1 to 80 characters, and a password of 10 to 128 characters with at least one letter and one number.
- Passwords are hashed with PBKDF2-SHA256 (310,000 iterations, random 16-byte salt). Session tokens are random, stored only as SHA-256 digests, and last seven days.
- Session cookies are `HttpOnly`, `SameSite=Lax` and `Path=/`. Sign-out ends the session on the server.
- Sign-in returns the same error for an unknown email and a wrong password. Five failures for one email within 15 minutes return 429.
- Every project and analysis belongs to the account that created it. Another account's analysis is reported as not found.
- Responses carry `Content-Security-Policy` (scripts from the same origin only), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy` and `Permissions-Policy`.
- Protected routes: dashboard pages redirect to sign-in when signed out. Protected API endpoints return 401 without a session. Public endpoints are `/health`, `/api/rules`, `/api/reference`, and `/api/auth/*`.
- OAuth, email verification and password reset are **not implemented**.

## Pages

| Route | Who can open it | Content |
| --- | --- | --- |
| `#home` | everyone | Homepage with an illustrative, labelled showcase. |
| `#signin`, `#signup` | everyone | Account forms. |
| `#docs/<section>` | everyone | Eighteen documentation sections with search, table of contents and previous/next links. The rule catalog is loaded from `/api/rules`. |
| `#api`, `#api/<endpoint>` | everyone | API reference rendered from `/api/reference`, with copy buttons on examples. |
| `#overview`, `#analyze`, `#findings`, `#projects`, `#reports` | signed-in users | The dashboard. |

The dashboard has project rename (Projects, then open a project).

**Live updates.** Each signed-in tab opens one event stream (`GET /api/events`). The server stores every event in the database before sending it, so a reconnect with `Last-Event-ID` replays what was missed, and events carry only identifiers, stage names, counts, and titles, never repository contents. The analysis island under the header moves through idle, queued, analyzing, findings discovered, completed, and failed, using only those events; it never shows a percentage, because the server does not compute one. If the stream fails repeatedly, the page falls back to polling. Views refresh from the API after events, and the stream closes on sign-out. Each account may hold 8 open streams and the server holds at most 64 in total; more are refused with HTTP 429.

**Motion.** Dashboard views change in sequence: the outgoing view fades and lifts out, then the incoming view arrives with a staggered entry while the capsule glides. Project and finding details open from their row or card: a ghost panel grows from the source into the drawer, and closing contracts it back, with focus returned to the row. The finding's severity and title, and the project's monogram and name, fly from the row into the drawer as clones while the originals are held, so no text appears twice. Long evidence lines scroll sideways inside the drawer, including at 390px and narrower. New and changed findings enter, flash once, or update their status in place without reordering. Escape closes the drawer and the profile menu. All of it is disabled under `prefers-reduced-motion`. The View Transitions API was tried for view changes and not used: it captured the whole page, which double-exposed the two views and froze the staggered entries.

## Analysis

Stages, in order: queued, retrieving repository, indexing files, inferring architecture, running rules, correlating and scoring, preparing report. An analysis that reaches its time budget completes as a partial result and says so. Cancellation takes effect at the next stage boundary.

Repository input is checked before download: only `https://github.com/` addresses are accepted, redirects are re-validated against the allowlist, and private repositories are not supported. ZIP archives are rejected for path traversal, symbolic links, excessive compression ratio, too many entries, or total size over the limit. Archives are unpacked into a temporary directory that is removed when the analysis ends.

### Rules

| ID | Category | Title | Severity | Confidence |
| --- | --- | --- | --- | --- |
| SEC-AUTHZ-001 | security | Sensitive route registered without an authorization guard | high | 0.6 |
| SEC-SECRET-001 | security | Hardcoded credential in source or configuration | critical | 0.9 |
| SEC-SECRET-002 | security | Real-looking secret in a committed env file | high | 0.8 |
| SEC-SQL-001 | security | SQL statement built by string concatenation | high | 0.85 |
| SEC-EVAL-001 | security | Dynamic code execution with a non-literal argument | medium | 0.6 |
| SEC-DEBUG-001 | security | Debug mode enabled in production configuration | medium | 0.8 |
| SLOP-STUB-001 | slop | Function returns a hardcoded success without performing its operation | high | 0.75 |
| GAP-VALID-001 | gap | Request input used without a visible validation step | medium | 0.5 |
| GAP-ERR-001 | gap | Network call without visible error handling | medium | 0.6 |
| LOOSE-TODO-001 | loose | TODO / FIXME marker left in code | low | 0.99 |
| LOOSE-DEP-001 | loose | Declared dependency is never imported | low | 0.6 |
| LOOSE-MOD-001 | loose | Module is never imported | low | 0.45 |

Risk score: severity weight times confidence, plus 2 per other finding in the same file, capped at 100.

## API

The full, route-checked reference is at `#api` in the running site and at `GET /api/reference` as JSON. A test calls every documented method and path and fails if one is not routed, or if a documented error code does not exist in the source.

Summary: `GET /health`; `GET /api/rules`; `GET /api/reference`; `POST /api/auth/signup`, `POST /api/auth/signin`, `POST /api/auth/signout`, `GET /api/auth/me`; `GET|PATCH /api/projects/{projectId}`, `GET /api/projects`; `GET|POST /api/analyses`, `POST /api/analyses/url`, `POST /api/repositories/inspect`; `GET /api/analyses/{id}`, `/files`, `/findings`, `/findings/{fid}`, `/investigation`, `/graph`, `/report?format=json|markdown|sarif`; `POST /api/analyses/{id}/cancel`, `POST /api/analyses/{id}/findings/{fid}/explain`; `PATCH /api/analyses/{id}/findings/{fid}`; `GET /api/ai/status`; `GET /api/events` (server-sent event stream, signed-in users only).

CORS: no CORS headers are sent, so only same-origin pages can call the API from a browser.

## Testing

| Command | What it covers |
| --- | --- |
| `python -m unittest discover -v` | 322 unit, integration and API tests (5 optional selenium browser tests skip unless selenium is installed): rules, corpus, ingestion, store, API, auth, authorization, project settings, reference, AI layer, the upload journey, the event stream (ordering, replay, heartbeats, per-account and global caps, isolation), rate limits, the cross-site guard, secret redaction, transactional persistence and migrations, the relationship graph, JSON, Markdown and SARIF 2.1.0 reports, and security headers over a real socket. |
| `python tools/investigation_journey.py http://127.0.0.1:8000` | Real analysis of the labelled multi-file fixture; investigation section and relationship graph on desktop and at 390px: risk, groups, evidence classes, diagram and list agreement, evidence on every relationship, keyboard selection, filter requests, no overflow, no page errors. 20 checks. |
| `python tools/live_journey.py http://127.0.0.1:8000 <zip>` | Two tabs on one account: live island states from events, the second tab updating without a reload, a dropped network and reconnect with a refetch, back/forward, mobile width, and reduced motion. 19 checks. |
| `python tools/motion_journey.py http://127.0.0.1:8000 <zip>` | Motion and layout checks in real Chromium: capsule states from events, view and drawer transitions, shared-element clones, project and finding ghosts, filter and status changes, reduced motion, 390px and 360px drawers and evidence scrolling. 28 checks. Also records a video. |
| `python tools/benchmark.py --large-files 1500 --out benchmarks/results.json` | Synthetic labelled corpus precision, recall and F1 (19 cases, labels written from the rule definitions, so this is a consistency check) plus a timed large generated tree. External repositories are reported separately and are not labelled. See AI_LOG.md, Phase 5. |
| `python tools/heldout_benchmark.py --set heldout2 --out benchmarks/heldout2_results.json` | Second held-out set (10 cases in `tests/fixtures/heldout2`, Python, JavaScript, Go, Java and PHP) for SSRF, weak cryptography, multi-line taint and command injection. Scored on labelled rule ids. It is still author-written, and the rules were designed after seeing its sink patterns, so it is a regression check, not independent accuracy. |
| `benchmarks/realworld_review.json` | Six hits of the new rules on third-party packages, reviewed by one author. All six were judged not to be vulnerabilities in context, which is why the new rules were lowered to low and medium severity. |
| `python tools/heldout_benchmark.py --out benchmarks/heldout_results.json` | Held-out synthetic corpus (6 cases in `tests/fixtures/heldout`) for the command-injection, XSS, unsafe-deserialization and path-traversal rules, scored on the rule ids each case labels. Written before those rules were implemented, but still author-written and small: it is a consistency check, not a measure of real-world accuracy. Unlabelled hits from other rules are reported separately. |
| `python tools/live_insertion_journey.py http://127.0.0.1:8000 <zip>` | A real analysis, no seeded findings: tab A on Findings with zero rows receives rows from a second tab's analysis; checks entry animation and stagger, DOM identities and severity tallies against the persisted API, the overview counter, and no duplicate rows after a dropped stream and reconnect. 15 checks. |
| `python tools/auth_journey.py http://127.0.0.1:8000` | Browser journey from a fresh session: protected redirect, validation, sign-up, reload, sign-out, sign-in, expiry. 21 checks. |
| `python tools/e2e_browser.py http://127.0.0.1:8000 <zip>` | Browser journey through the dashboard: sign-up, analysis, findings, detail, triage, projects, exports, responsive checks. 42 checks. |
| `python tools/pages_journey.py http://127.0.0.1:8000 <zip>` | Documentation, API reference, navigation and footer links, mobile overflow, and project rename. 23 checks. |

The browser scripts need Playwright for Python with Chromium. Pass the server URL, and for the two dashboard scripts a ZIP of a repository (the labelled corpus in `tests/fixtures/corpus` works).

## Known limitations

- Detection is rule-based. It misses issues no rule describes and reports some patterns that are harmless in context. Accuracy has been measured only on the labelled fixtures in this repository, which were written for this project, so the figures are not independent.
- Rate limits are in memory and reset on restart. They cover sign-up (10 per hour per client address), sign-in (30 per 15 minutes per address, and 5 failures per 15 minutes per email), analysis starts (40 per hour per account), other inspection requests (120 per hour per account), and 3 concurrent analyses per account. Client addresses come from the socket, so a reverse proxy makes every visitor share one address until a trusted-proxy policy is added.
- Cross-site protection is an `Origin` check on cookie-authenticated POST and PATCH requests plus the `SameSite=Lax` cookie. There is no CSRF token. Requests without an `Origin` header are not checked, which matches non-browser clients.
- The server speaks plain HTTP. Set `NEXUS_COOKIE_SECURE=1` only when a TLS proxy sits in front of it.
- Private GitHub repositories are not supported; no access tokens are accepted.
- The AI explanation has been tested only with a fake provider. It has not been run against a live provider in this environment.
- Each open event stream holds one server thread for up to four minutes. The per-account cap is 8 and the global cap is 64.
- Events are stored per account and pruned to the newest 2,000. Per-analysis finding events stop at 500, so a very large result is reported by its completion event, not by one event per finding.
- Sign-up returns 409 `email_taken` for an address that is already registered, so the endpoint reveals which emails have accounts. Rate limits are the only mitigation.
- Session cookies carry the `Secure` flag only when `NEXUS_COOKIE_SECURE=1`. The server logs a warning at startup when the flag is not set.
- The Content-Security-Policy allows inline `style` attributes (`'unsafe-inline'` for styles only). Scripts must come from the same origin.
- JavaScript and TypeScript import extraction reads one line at a time, so multi-line imports are missed. Missed relationships are absent from the graph; none are invented. Python files that fail to parse are skipped for import extraction.
- Secret findings still show the first three characters of the matched value in their excerpt.
- The graph shows findings, groups, and import relationships only. It does not follow calls or data flow.
- The official SARIF 2.1.0 schema was not retrievable in the build environment, so exports are checked against the stdlib checker and a JSON Schema subset, not the full schema.
- Private repository support, project deletion, email verification, and password reset are not implemented.

## Relationship graph

The analysis drawer shows a relationship graph after an analysis completes. `GET /api/analyses/{id}/graph` builds it on the server from stored findings, root-cause groups and import edges. Each edge carries an explanation and the evidence behind it: a finding location, an import statement with its line, a shared-pattern membership, or a directory containment. Edge statuses are `confirmed` (a rule or a statically resolved import), `potential` (an impact path or hypothesis that needs review, drawn dashed), `informational`, and `structure` (directory layout, hidden unless requested).

The graph is a focused view, not a call graph. It shows at most 120 nodes and 600 edges by default and says when it has cut anything. Python imports come from the `ast` module, and JavaScript and TypeScript relative imports come from a line-based pattern. Import edges exist only where the source resolves to a file in the repository.

## Reports and exports

`GET /api/analyses/{id}/report?format=json|markdown|sarif` assembles one report and renders it in each format. JSON uses the schema `nexus-inspect.report/v2`. Markdown escapes repository text so it cannot inject formatting. SARIF 2.1.0 uses the `application/sarif+json` media type and the OASIS schema URI, and it includes rule metadata, relative artifact URIs, clamped regions and partial fingerprints. A failed analysis is reported as an unsuccessful execution, and an empty result says that no defects are guaranteed.

Validation: the tests check SARIF structure with a stdlib checker (`tests/sarif_check.py`) and with a JSON Schema subset (`tests/fixtures/sarif-2.1.0-subset.schema.json`). The official OASIS schema could not be downloaded in the build environment, so full schema validation against it has not been run.

