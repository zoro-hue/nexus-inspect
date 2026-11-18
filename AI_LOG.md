--- a/AI_LOG.md
+++ b/AI_LOG.md
@@ -1,232 +1,369 @@
 # AI assistance log
 
-This project was built with an AI coding assistant (Claude, Anthropic). This log lists what the assistant produced, what was run to check it, what failed along the way, and what is not done. It does not claim more than was executed. No human review of the code has been recorded; any review by a person is not reflected here.
+This log records how NEXUS Inspect was built with an AI coding assistant (Claude, Anthropic). It separates the original development record, produced by Claude for the v3 release, from later AI-assisted follow-up work, including the v4 Nexus AI assistant fix. For each area it describes the current capabilities, what was run to validate them, the defects found and corrected, and the boundaries of what has been validated. The log records only work that was executed and does not claim more than was run. Review status is described in [Review and Quality Assurance](#review-and-quality-assurance).
+
+## Current release
+
+| Release | File | Size (bytes) | SHA-256 | Content |
+| --- | --- | --- | --- | --- |
+| v4 (current) | `nexus-inspect-release-v4.zip` | 1,022,017 | `e4e5df3dc5cb3aacb2fdf7954c6d80fe7b20b9be0dd72d881106b15f308292f6` | v3 codebase plus the Nexus AI assistant query fix and its regression tests (AI-assisted follow-up, 2026-11-18) |
+| v3 | `nexus-inspect-release-v3.zip` | 627,420 | `499a49fe7ef9ec8b760b76b7209f545f528c68f752c0e521f3215f2178351503` | Original Claude-authored release |
+| v2 | `nexus-inspect-release-v2.zip` | 608,957 | `380ee09dc9da426e7f9e7de446d6f4ee0f8eda9e38ed09d8c15a829d2091fa6d` | Earlier release, retained for reference |
+
+Nothing has been pushed to GitHub, and nothing has been submitted to Arena, in connection with the v4 release. Earlier release archives remain in the outputs folder for traceability. The v4 archive was built from a clean copy that excludes the data directory, caches, screenshots, logs, databases and environment files.
 
 ## Tools
 
 - Coding assistant for backend, frontend, tests and documentation.
-- Verification tools: Python 3 standard library, `unittest`, Selenium with headless Chromium, `curl` against the local server.
-- At runtime the application calls an AI model via Google Gemini API (`gemini-2.0-flash`) as primary provider, with configurable support for Anthropic and OpenAI when `NEXUS_AI_API_KEY` is configured. When unconfigured or offline, the system falls back to deterministic rule engine explanations.
+- Verification tools: Python 3 standard library, `unittest`, headless Chromium through Selenium and Playwright (optional, used by the browser journeys), and `curl` against the local server.
+- At runtime the application calls an AI model through the Google Gemini API (`gemini-2.0-flash`) as the primary provider. Anthropic and OpenAI are supported as optional providers through `NEXUS_AI_PROVIDER`. When no provider key is configured, or the provider fails, the assistant answers from the deterministic rule engine and states the reason.
 
-## What was built
+## Current capabilities
 
-- **Analysis engine**: ingestion of public GitHub repositories (HTTPS, host allowlist, redirects re-validated) and ZIP archives (traversal, symlink, compression-ratio, entry-count and size limits). Eighteen deterministic rules across four categories (security, AI slop, coding gaps, loose ends). Redacted excerpts, a file inventory, risk scoring, and scan diff comparisons.
-- **AI Explanation & Investigation Engine**: Google Gemini API (`gemini-2.0-flash`) integration with multi-provider architecture (`NEXUS_AI_PROVIDER=gemini|anthropic|openai`). Context-aware prompt synthesis with prompt-injection defense boundaries (`REPO_DATA_BOUNDARY`), secret value masking, and strict JSON output validation. Structured explanation output covering issue meaning, detection rationale, real-world impact, remediation guidance, corrected code snippets, verification steps, and confirmed/potential evidence classification.
-- **Persistent Explanation Caching**: SQLite-backed explanation caching (`ai_explanations`, `ai_group_explanations`) in `Store` to prevent redundant API queries. Automatic cache invalidation when a finding's triage status is updated.
-- **Floating Nexus AI Assistant**: Compact bottom-right capsule (`#nexusAiCapsule`) and spring-expanding conversational panel (`#nexusAiPanel`) with route-aware context badges, dynamic suggestion chips, markdown rendering, and shared backend AI/deterministic intelligence.
-- **Persistence**: SQLite with versioned migrations (schema versions 1 through 7): projects, analyses, findings with status history, analysis files, users and sessions, events table, and persistent AI explanation caches. Projects carry an owner.
-- **Live updates**: `GET /api/events` is a server-sent event stream scoped to the signed-in account. It replays from `Last-Event-ID`, sends a named heartbeat every 15 seconds, and caps each account at 8 open streams. The page keeps one stream per tab, reconnects after drops or when the browser reports the network is back, watches for silent connections, and falls back to polling. The analysis island is driven by those events; overview numbers tween to their persisted values.
-- **Rate limits and guards**: sign-up, sign-in, analysis starts and inspection requests are rate limited in memory with `Retry-After`; each account may run three analyses at once; cookie-authenticated POST and PATCH requests with a cross-site `Origin` are refused.
-- **Accounts**: sign-up, sign-in, sign-out and session lookup. PBKDF2-SHA256 password hashing, digest-stored session tokens, HttpOnly SameSite=Lax cookies, seven-day sessions, in-memory sign-in throttle, and per-account isolation of projects and analyses.
-- **API**: the endpoints listed in `README.md`, plus `GET /api/reference`, a catalog that the API reference page renders.
-- **Dashboard**: overview, analyze, findings, projects and reports, all wired to the API. Project rename. Sign-in and sign-up pages, route protection, expired-session handling, and a profile menu with sign-out.
-- **Public pages**: documentation (eighteen sections, search, table of contents, previous and next links, rule catalog loaded from the API) and the API reference (every endpoint, parameters, bodies, responses, examples, copy buttons). Footer and Documentation menu links point to these pages.
-
-## Verification actually run
-
-Run on the final state of this session.
-
-- Arena commands from `arena.json`, in a clean copy made with `tar` and no database: `python -m pip install -r requirements.txt` exit 0; `python -m unittest discover -v` exit 0 with 163 tests passing; `python server.py` answered `GET /health` on port 8000 with `{"ok": true, ...}` and returned 200 for `/`.
-- `python -m unittest discover -v` with `-W error::ResourceWarning`: 163 tests pass, including the event stream tests (ordering, replay, heartbeats, isolation, per-account cap) and the rate-limit and cross-site tests.
-- `tools/live_journey.py` against a clean server: 19 of 19 checks pass. Two tabs on one account; the island passes through queued, analyzing, findings and completed from events; the second tab updates without a reload and matches the persisted total; a finding changed through the API while tab A was offline is refetched after reconnect; back, forward and refresh keep their routes; mobile at 390px has no overflow; reduced motion skips View Transitions and keeps the stream live; no page or console errors.
-- `tools/auth_journey.py` against a clean server: 21 of 21 checks pass.
-- `tools/e2e_browser.py` against a clean server: 42 of 42 checks pass. It now signs up through the real form first.
-- `tools/pages_journey.py` against a clean server: 23 of 23 checks pass. Covers the documentation TOC and search, pager and browser back, rule catalog from the API, API reference count and search, copy button, Documentation menu, footer links, dead-link scan, 390px overflow on the public pages, and project rename from the dashboard (including empty-name validation and an anonymous 401).
-
-These browser checks ran in headless Chromium at 1366px and 390px. Screenshots were not reviewed in this session.
-
-## Bugs found by testing and fixed
-
-- Sign-out message was consumed by a `hashchange` event before the reload. Fixed by changing the URL with `history.replaceState`.
-- The browser suite could not reach the Reports exports after the buttons stopped being links. Fixed by storing each URL in `data-href`.
-- `GET /api/auth/me` returned 401 for signed-out visitors, which logged a console error on every page load. It now returns 200 with `user: null`.
-- The dead-link check in the browser suite did not recognise router routes. Fixed.
-- The API reference overflowed a 390px viewport because long example JSON stretched a grid track. Fixed with `minmax(0, 1fr)`.
-- Some statements in the documentation did not match the rule definitions (slop description, gap and loose-end rules, risk score, a Python version claim). Corrected to match `nexus/rules.py` and `nexus/scanner.py`.
-- The API reference lists error codes; a test now fails if a listed code does not exist in the source.
-- Live-update tests counted the new heartbeat frames as analysis events. Fixed by excluding heartbeats from the test reader, and a test now checks the heartbeat itself.
-- The event stream was missing from the API reference. Added, and the route test checks streaming routes against `server.py`.
-- Going offline does not close an open `EventSource` in Chromium, so the page did not notice a dropped stream. Fixed: the page closes the stream on the browser's offline event, reconnects on online, and has a watchdog for connections that go silent.
-- A stream refused with HTTP 429 left the page in "reconnecting" forever, because `EventSource` does not retry refused streams. Fixed with a scheduled retry while the session is still valid.
-- A superseded View Transition rejected an internal promise, which surfaced as a page error. Fixed by handling those promises.
-
-## Security review (what was checked)
-
-- Passwords: PBKDF2-SHA256, random salt, comparison with `hmac.compare_digest`; a dummy hash is computed for unknown emails so timing does not reveal accounts.
-- Sessions: random 32-byte tokens, stored as SHA-256 digests; sign-out and expiry are enforced server-side; forged and expired cookies are rejected by tests.
-- Authorization: analysis, finding, project and report routes all check the owner; other users receive 404. Tests cover read, write and export access.
-- CSRF: state changes use JSON bodies and a cookie set with SameSite=Lax. Cross-site form posts do not carry the cookie. No token-based CSRF defence is implemented.
-- CORS: no CORS headers are sent.
-- Error disclosure: API errors use a fixed `{code, message}` shape. Unexpected server exceptions are logged on the server, not sent to the client.
-
-- Rate limits: sign-up, sign-in, analysis starts and inspection requests are limited per address or per account, with `Retry-After`. Limits are in memory and reset on restart. Tests cover the limiter and the 429 responses.
-- Cross-site writes: an `Origin` that differs from `Host` on a cookie-authenticated POST or PATCH is refused with 403. There is no CSRF token; requests without `Origin` are not checked.
-- Event streams: authorized per account; a stream holds one server thread, capped at 8 per account; each frame carries only whitelisted fields.
-
-Not yet checked: secure-cookie behaviour behind a TLS proxy (not tested); a trusted-proxy policy for client addresses (the limiter uses the socket address); a formal dependency review (there are no third-party Python dependencies, and the frontend uses no npm packages in the page).
-
-## Not done
-
-- A global cap on event streams, a trusted-proxy policy, and an optional CSRF token.
-- A View Transitions API route change: tried, rejected, documented in README. Dashboard views use a sequenced fade and staggered entry instead.
-- Per-finding insertion animation in a live stream: findings are saved only when an analysis completes, so they arrive as one burst. The insertion path is implemented, but in a real browser it was exercised through status changes, not through a live insertion.
-- Interactive API playground.
-- Onboarding redesign beyond the existing Analyze page.
-- Profile settings (display name, password change).
-- OAuth sign-in, email verification and password reset.
-- Live-provider AI verification.
-- Private repository support.
-
-## Human review
-
-No human review of the code, documentation or claims is recorded in this log.
-
-## Motion polish pass (final)
-
-- Shared-element continuity: the finding severity and title, and the project monogram and name, now fly from the row into the drawer as clones while the originals are held hidden. Verified in `tools/motion_journey.py` (two clones per open, originals hidden).
-- Bug found by the new test and fixed: the success path of finding open passed its wiring callback as the drawer source, and the status-select listener ran before the deferred body rendered. The callback now runs when the body is rendered.
-- Bug found by the motion journey and fixed: the drawer close button sat under the detail header on project drawers (stacking), so a click on close was intercepted. The close button now has a stacking level above the body.
-- Evidence scrolling: long lines scroll sideways inside the excerpt, with an edge shadow when more content is available. The 390px and 360px drawers have no page overflow.
-- Test-side corrections: the finding waits now target the loaded content (`data-fid`) rather than the loading panel, and the mobile evidence check opens the finding that has a 93-character evidence line.
-- Live insertion: `tools/live_insertion_journey.py` runs a real analysis with tab A on Findings from zero rows. 15 of 15 checks pass.
-- Results: unit tests 163 of 163; live journey 19; auth journey 21; e2e browser 42; pages journey 23; motion journey 28; live insertion 15. All passed on the working copy and in the Arena clean copy (unit tests and startup).
-- Not measured in this pass: island width per state, main-thread long tasks and layout cost during flights, and real-device behaviour.
+### Repository ingestion and analysis
+
+- Public GitHub repositories are ingested over HTTPS through a host allowlist, with redirects re-validated at each hop. ZIP archives are accepted with protection against path traversal, symlinks, excessive compression ratios, excessive entry counts and oversized content. Limits are 25 MB uploaded, 100 MB extracted and 10,000 entries.
+- The deterministic rule set contains 24 rules across four categories: security, AI-generated code patterns ("AI slop"), coding gaps and loose ends. The set began with 18 rules in v3. Six rules were added later (see [Development history](#development-history)).
+- Evidence is classified as confirmed, potential or informational. Potential findings are labelled as such and are not presented as confirmed defects.
+- Python files receive an abstract syntax tree pass. JavaScript and TypeScript use pattern rules and relative-import resolution. Generated and minified files are skipped. Excerpts are redacted, and secret values are masked.
+- A single-file taint pass (`nexus/flow.py`) tracks request input from common web framework entry points through assignments and concatenation to sinks, and clears it at sanitizers. It operates within one file only.
+- Each analysis stores a file inventory, risk scoring, per-analysis coverage, scan diffs, and a relationship graph in the analysis drawer. Every graph edge is built from evidence: a finding location, an import statement with its line, a group membership, or a directory containment.
+- Investigation grouping clusters related findings into root-cause groups, with a per-group explanation that can be produced by the AI layer.
+
+### AI explanations and the Nexus AI assistant
+
+- The provider layer (`nexus/ai.py`) supports Gemini (default), Anthropic and OpenAI. Prompts carry an explicit repository-data boundary against prompt injection, secret values are redacted before any external call, and model output is validated strictly against the expected JSON structure.
+- Explanations are cached in SQLite (`ai_explanations`, `ai_group_explanations`). The cache is invalidated when a finding's triage status changes.
+- The floating assistant offers a compact capsule and an expandable panel. It uses route-aware context (current page, analysis and open finding), suggestion chips and markdown rendering.
+- When the live provider is unavailable, the assistant answers from the deterministic knowledge base. Since v4 the notice names the reason, for example `Live AI is unavailable (ai_auth_failed)`. Failure codes are logged without key material.
+- The suggested questions "How fast does deterministic analysis run?" and "What repository formats are supported?" are answered from the code's own ingestion limits and the saved benchmark result. They are not hardcoded numbers.
+
+### Accounts, persistence and live updates
+
+- Sign-up, sign-in and sign-out use PBKDF2-SHA256 password hashing, digest-stored session tokens, HttpOnly SameSite=Lax cookies and seven-day sessions. Projects and analyses are isolated per account.
+- SQLite storage uses versioned migrations (schema versions 1 through 7): projects, analyses, findings with status history, analysis files, users and sessions, an events table, and persistent AI explanation caches.
+- `GET /api/events` is a server-sent event stream scoped to the signed-in account. It replays from `Last-Event-ID`, sends a heartbeat every 15 seconds and allows up to 8 open streams per account, with a global cap of 64. The page reconnects after drops, detects silent connections, and falls back to polling.
+- Rate limits apply to sign-up, sign-in, analysis starts and inspection requests, with `Retry-After`. Each account may run three analyses at once. The server allows eight running analyses globally (`NEXUS_MAX_ACTIVE_ANALYSES`).
+
+### Interface, API and documentation
+
+- The dashboard covers overview, analysis, findings, projects and reports, with project rename, route protection and expired-session handling.
+- The public documentation (eighteen sections with search, table of contents, pager and rule catalog loaded from the API) and the API reference (every endpoint, parameters, bodies, responses and copy buttons) are both served by the application. `GET /api/reference` returns the catalog that the reference page renders.
+- Reports export as JSON, Markdown and SARIF 2.1.0. SARIF output is checked against a required-field subset (see [Known technical limitations](#known-technical-limitations)).
+- The homepage includes a section-transition system and an investigation workspace. The workspace uses a sample repository, `sample-shop-api`, which is labelled as illustrative and is not connected to any real analysis.
 
-## Phases 1 to 4 (reconstructed from the code)
+## Development history
 
-Note: no session record for Phases 1 to 4 was kept in this project. The entries below were reconstructed from the test modules' own descriptions and the features listed under "What was built". They carry no dates, test counts or measured results from those phases, and they are not a record of the work as it happened.
+### Phases 1 to 4 (reconstructed from the code)
+
+Note: no session record for Phases 1 to 4 was kept in this project. The entries below were reconstructed from the test modules' own descriptions and the features listed under Current capabilities. They carry no dates, test counts or measured results from those phases, and they are not a record of the work as it happened.
 
 - **Phase 1, backend core** (`tests/test_phase1.py`, `tests/test_ingest.py`, `tests/test_rules.py`, `tests/test_store_api.py`, `tests/test_server_scanner.py`, `tests/test_corpus.py`): repository ingestion from GitHub and ZIP archives with traversal, symlink, compression-ratio, entry-count and size limits; file inventory; redacted excerpts; asynchronous analysis with cancellation; branch inspection; the first rule set; the store and JSON API; a labelled fixture corpus.
 - **Phase 2, accounts and the first end-to-end journey** (`tests/test_auth.py`, `tests/test_security.py`, `tests/test_journey.py`): sign-up, sign-in and sign-out; PBKDF2-SHA256 password hashing; digest-stored session tokens; per-account isolation of projects and analyses; rate limits and the concurrency cap on analyses; cross-site write refusal. `tests/test_journey.py` runs a labelled repository through the real API.
-- **Phases 3 and 4 (phase labels not recorded in the files)**: live updates and the dashboard (`tests/test_events.py`, `tests/test_browser_journey.py`, the browser journeys listed under Verification); the AI layer (`tests/test_ai.py`, `tests/test_gemini_integration.py`, `tests/test_assistant.py`): an optional provider, redaction before any external call, strict validation of model output, a deterministic fallback when no key is configured, and the floating assistant. Motion and homepage work is covered under its own headings below.
+- **Phases 3 and 4** (phase labels are absent from the files): live updates and the dashboard (`tests/test_events.py`, `tests/test_browser_journey.py`, and the browser journeys listed under Validation coverage); the AI layer (`tests/test_ai.py`, `tests/test_gemini_integration.py`, `tests/test_assistant.py`), with an optional provider, redaction before any external call, strict validation of model output, a deterministic fallback when no key is configured, and the floating assistant.
+
+The baseline at the end of these phases was 163 unit tests passing under the Arena commands in a clean copy (see Validation coverage).
+
+### Motion polish pass
 
-## Phase 5: detection, investigation, benchmark
+- View transitions: an API-driven route change was tried and rejected, and is documented in the README. Dashboard views use a sequenced fade and staggered entry instead.
+- Shared-element continuity: the finding severity and title, and the project monogram and name, fly from the row into the drawer as clones while the originals are hidden. Verified in `tools/motion_journey.py` (two clones per open, originals hidden).
+- Live insertion: `tools/live_insertion_journey.py` runs a real analysis with tab A on Findings starting from zero rows. 15 of 15 checks passed.
+- Evidence lines scroll sideways inside the excerpt, with an edge shadow when more content is available. The 390px and 360px drawers have no page overflow.
+- Defects found and corrected in this pass are listed under [Defects found and corrected](#defects-found-and-corrected).
+- Results: unit tests 163 of 163; live journey 19; auth journey 21; end-to-end browser 42; pages journey 23; motion journey 28; live insertion 15. All passed on the working copy and in the Arena clean copy.
+- Island width per state, main-thread long tasks during transitions, and real-device behaviour were not measured in this pass.
+
+### Phase 5: detection, investigation and benchmark
 
 Scope: advanced detection (AST pass for Python, cross-file broken-import rule, evidence classes, generated and minified skipping, pattern-only deduplication, per-analysis coverage stored in schema migration 5), an investigation layer (deterministic grouping, risk methodology, root-cause groups, per-group AI explanation with validation and a transparent fallback), and a labelled benchmark with a corpus and a large generated tree.
 
-Policy decisions made during external review:
+Scope decisions recorded during the external review pass:
+
 - `GAP-ERR-002`, `GAP-VALID-001`, `LOOSE-STUB-001` and `SLOP-STUB-001` apply to production code only. Test, example and documentation paths are excluded. Trade-off: real issues in those paths are no longer reported by these heuristics. Security rules (`SEC-*`) still apply everywhere.
 - `test.js`, `test.ts`, `test.py` and `tests.py` are treated as test files.
 - `LOOSE-MOD-001` does not flag test files, because a test runner loads them rather than an import.
 
-Measured results (labelled by source):
+Measured results, labelled by source:
+
+- Synthetic labelled corpus (19 cases, `tools/benchmark.py`): 25 true positives, 0 false positives, 0 false negatives. Precision, recall and F1 are all 1.0. The labels were written from the rule definitions, so this is a consistency check rather than evidence of real-world accuracy.
+- Synthetic generated large tree (1,500 files): 4.23 s on this machine.
+- External repositories (not labelled, no precision claim): flask (94 files) 3 findings, itsdangerous (20 files) 2 findings, is-number (7 files) 1 finding. Before the scope policy changes, flask produced 64 findings. The remaining findings were reviewed by hand: two `SEC-EVAL-001` on intentional config loading, one `LOOSE-DEAD-001` on a statement after a module-level raise (correct), one `LOOSE-TODO-001` (correct), and one `GAP-ERR-002` on a swallowed exception with an explanatory comment after the handler (a known false-positive class, retained).
+
+Results at the end of Phase 5: unit tests 241 of 241; browser journeys investigation 13 of 13, live 19, live insertion 15 of 15, auth 21, end-to-end 42, pages 23, motion 28.
+
+### Phase 6: audit, relationship graph, report exports and Arena readiness
+
+Scope: a security and reliability audit across Phases 1 to 5 with a regression test for each confirmed defect; an evidence-backed relationship graph in the analysis drawer; JSON, Markdown and SARIF 2.1.0 exports built from one assembled report; Arena packaging and documentation.
+
+- Audit fixes are listed under [Defects found and corrected](#defects-found-and-corrected) and [Security and reliability considerations](#security-and-reliability-considerations).
+- Graph and report correctness: impact paths are `potential` and each cites the import that creates the path. Informational findings create no impact paths. Phase 6 tests check that every import edge names a real repository file and that every edge carries an explanation and a known status.
+- SARIF was checked with the stdlib checker in `tests/sarif_check.py` and a JSON Schema subset (`tests/fixtures/sarif-2.1.0-subset.schema.json`) under jsonschema 4.26.0 in a separate environment. The fixture export validated with zero errors. The official OASIS schema could not be downloaded from this environment, so full validation against it is a planned validation step (see Known technical limitations).
+- Results: unit tests 285 of 285 (Phase 6 added 44 tests in `tests/test_phase6.py`, including per-account stream isolation). Browser journeys: investigation 20 of 20 (includes the graph), live 19, live insertion 15, auth 21, end-to-end 42, pages 23, motion 28, each in headless Chromium against a restarted server with no page errors. The graph was reviewed at 1366px and 390px; other screens were not reviewed in this phase.
+- Benchmark (synthetic, `tools/benchmark.py --large-files 1500`): 19 cases, 25 true positives, 0 false positives, 0 false negatives. The 1,500-file generated tree took 4.44 s on this machine.
+
+### Homepage section transitions
 
-Synthetic labelled corpus (19 cases, `tools/benchmark.py`): 25 true positives, 0 false positives, 0 false negatives. Precision, recall and F1 are all 1.0. These labels were written from the rule definitions, so this number is a consistency check, not evidence of real-world accuracy.
+- `home.js` adds a section module (`initSectionBoundaries`, with `cleanupSectionBoundaries` on teardown). Each section boundary receives a hairline whose progress is scroll-scrubbed. A light rises behind each section as it enters. Only changed values are written, so the page does no work while idle.
+- `home.css` uses transform and opacity only. Reduced motion shows the final state immediately. The sticky How-it-works column is not inside a transformed ancestor, so it still sticks.
+- Checks in real Chromium at 1440px and 390px: `tools/home_section_transitions_check.py` 14 of 14; `tools/home_transitions_check.py` 12 of 12; `tools/home_motion_check.py` step indicator, track fill, markers and sticky offset all pass. The checker disables smooth scrolling on its own test pages only; the product keeps it.
+- Unit suite: 315 tests, 5 skipped. The earlier import error from `tests/test_browser_journey.py` was resolved, and that test now skips cleanly when Selenium is missing.
+- Recession effects (sections shrinking as the next one arrives) were deferred. They would require a transform on the section, which would conflict with the sticky column.
 
-Synthetic generated large tree (1,500 files): 4.23 s on this machine.
+### Homepage investigation workspace
 
-External repositories (not labelled, no precision claim): flask (94 files) 3 findings, itsdangerous (20 files) 2 findings, is-number (7 files) 1 finding. Before the policy changes, flask produced 64 findings. The remaining findings were reviewed by hand: two `SEC-EVAL-001` on intentional config loading, one `LOOSE-DEAD-001` on a statement after a module-level raise (correct), one `LOOSE-TODO-001` (correct), and one `GAP-ERR-002` on a swallowed exception with an explanatory comment after the handler (a known false-positive class, left in place).
+- The former self-drawing graph in `#investigation` was replaced by a three-panel workspace: explorer, code with highlighted finding and trace lines, and a findings panel with severity, confidence, evidence and a relationship trace.
+- Selection uses one delegated click listener in `home.js` (`initInvestigation` / `cleanupInvestigation`). It makes no backend calls. The default state is visible without JavaScript. On narrow screens the explorer becomes file chips and the panels stack.
+- `tests/test_home_workspace.py` guards against the removed graph markup returning.
+- Verified in headless Chromium at 1440, 1280, 1024, 768, 390 and 360px: no document overflow, no console errors, selection updates both the code and findings panels, and reduced motion leaves no running animations.
+- Button feedback: primary and light calls to action lift and glow on hover, and the pressed state takes precedence (`home.css?v=5`).
+- Hero entrance, navigation dropdown, logged-out and logged-in headers, and the mobile menu were verified in headless Chromium. The session cookie is HttpOnly and no token is stored in localStorage.
+- Scope of this pass: the homepage is not a full redesign of every section, and screen recordings were not produced.
 
-Test results on the final working copy:
-- Unit tests: 241 of 241 pass (Phases 1 to 5, including regression tests for the scope policy above).
-- Browser journeys: investigation 13 of 13, live 19, live insertion 15 of 15, auth 21, e2e 42, pages 23, motion 28.
-- One earlier live insertion run recorded 14 of 15 with one failure. I did not capture its name, and it did not reproduce on rerun. Treat it as unexplained.
-- Motion and live insertion timed out during sign-up after repeated runs exhausted the in-memory sign-up rate limit. A server restart cleared it.
+### Security rule coverage pass
 
-Not done or not verified:
-- The per-group AI explanation was not called against a live provider, because no provider key is available in this environment. The validation code and the fallback are covered by unit tests only.
-- External precision was not labelled. Counts alone are reported above.
-- AST analysis covers Python only. JavaScript and TypeScript use pattern rules and relative-import resolution, not a parser.
-- Cross-file correlation is limited to the deterministic grouping and broken-import rule. It is not a dataflow analysis.
-- The items above are the Phase 5 limits. Phase 6 limits are listed under its own heading.
+- Four rules were added to cover sinks named in the brief: `SEC-CMD-001` (command injection), `SEC-XSS-001` (cross-site scripting sinks), `SEC-DESER-001` (unsafe deserialization) and `SEC-PATH-001` (path traversal). All are `potential` evidence and match a sink on one line with non-literal input from the same line.
+- A false positive was corrected: `SEC-EVAL-001` treated a destructured JavaScript `exec(` from `child_process` as dynamic evaluation. JavaScript now matches only `eval(`. Python still matches `eval` and `exec`.
+- Held-out check: six cases in `tests/fixtures/heldout` were written before the new rules existed, with labels set from file contents. `tools/heldout_benchmark.py` scored 6 true positives, 0 false positives and 0 false negatives on the labelled rule identifiers. This set is small and author-written, and it was later used for tuning (see the data flow pass).
+- Across all rules, the same run produced 7 false positives from unlabelled rules, all from `LOOSE-MOD-001` (standalone files that no other file imports, a low-confidence heuristic that fires on entry-point scripts) and `GAP-VALID-001`. Overall precision across all rules on that corpus was 0.46. `LOOSE-MOD-001` was not tuned in this pass.
+- Sixteen snippet variants were used to find two misses, which were then corrected. They are regression checks in `tests/test_code_rules.py`, not held-out results.
+- Unit tests after this pass: 326 run, OK, 5 skipped.
 
-## Phase 6: audit, relationship graph, report exports, Arena readiness
+### Data flow, SSRF, weak cryptography and calibration pass
 
-Scope: a security and reliability audit across Phases 1 to 5 with regression tests for each confirmed defect; an evidence-backed relationship graph in the analysis drawer; JSON, Markdown and SARIF 2.1.0 exports built from one assembled report; Arena packaging and documentation.
+- `nexus/flow.py` adds sequential single-file taint tracking. Request input enters through common web frameworks (Python, JavaScript), Go `net/http`, Java servlets and PHP superglobals. It flows through assignments and concatenation, and is cleared by a sanitizer or literal reassignment. Findings name the line where the input entered.
+- `SEC-CMD-001` now covers Go (`sh -c`), Java (`Runtime.exec`) and PHP (`system` family), and uses taint for Python and JavaScript sinks. `SEC-PATH-001` uses taint for file-access sinks in Python, JavaScript, Go, Java and PHP.
+- New rules: `SEC-SSRF-001` (outbound URL built from request input; potential, medium) and `SEC-CRYPTO-001` (MD5, SHA-1, DES, RC4 and ECB; potential, low). The rule count is now 24.
+- Calibration after real-world review: `SEC-CRYPTO-001` was lowered to low, and `SEC-DESER-001` to medium, after all six hits from these two rules on third-party packages were judged not to be vulnerabilities in context (see `benchmarks/realworld_review.json`). This was a single-author review and is not independent.
+- False-positive handling: runnable scripts (shebang or `__main__` guard) are exempt from `LOOSE-MOD-001`. `GAP-VALID-001` recognises common validation libraries (express-validator, celebrate, class-validator, ajv, wtforms, cerberus, Pydantic `BaseModel`, Zod and Joi). A further change that skipped `LOOSE-MOD-001` in repositories with no internal imports removed four labelled detections and was reverted. The remaining noise on standalone files is documented rather than suppressed.
+- A `child_process` call routed through `require(...).exec` is detected. A RegExp `.exec(` call is not.
+- Held-out sets: set 1 (6 cases) was used to tune the first four rules and is no longer independent. Set 2 (10 cases) was written before the rules and then used for regression, so it is also not independent. Labelled-scope results: set 1 6 of 6, set 2 10 of 10. All-rule false positives on set 2 were 14, all from unlabelled rules (`GAP-ERR-001` and `LOOSE-MOD-001`).
+- Unit tests: 347 run, OK, 5 skipped. Added tests cover symlinks, oversized and binary files, invalid encodings, nested archives, file-count limits and prompt-injection text in `tests/test_untrusted_repository.py`, plus `tests/test_code_rules.py` and `tests/test_flow.py`.
 
-### Audit findings fixed (each has a regression test in `tests/test_phase6.py` or `tests/test_security.py`)
+### v4 follow-up: Nexus AI assistant query failure (AI-assisted, 2026-11-18)
 
-- Partial findings were saved after a failed batch. Saves now run in one transaction with rollback.
+This follow-up was made after v3. It was carried out by the AI assistant against the v3 codebase, under a brief to diagnose before changing code.
+
+**Symptom.** The chat panel displayed `Error: Unable to process query. Please try again.` for queries that should have been answered.
+
+**Diagnosis.** Three findings were established before any change was made:
+
+1. **Error shape mismatch (frontend).** The API returns errors as `{"error": {"code", "message"}}`, but `assistant.js` read a top-level `message`. Every error response therefore displayed the generic text. A stale `analysisId` reproduced this: the API returned 401 with `Sign in to query repository analysis.`, which the page could not show.
+2. **Silent provider failure (backend).** `chat()` caught every exception from the provider with `except Exception: pass`. With a dummy key, Gemini returned `ai_auth_failed` (in this sandbox the request was blocked by the egress allowlist, which reports HTTP 403). The failure produced a fallback answer with no trace.
+3. **Generic answers to suggested questions.** The deterministic fallback had no branches for the two suggested questions. The reply also contained the overclaim "executes instantly", which has been corrected.
+
+**Changes.**
+
+- `assistant.js`: the error branch reads `body.error.message` first, and falls back to the generic text only when no message is present.
+- `nexus/assistant.py`: provider errors are caught by code. Known `AIError` codes are logged with the code and HTTP status only. Unexpected exceptions are logged by exception type only. The fallback notice names the code. The two suggested questions are answered from ingestion limits in the code and from `benchmarks/results.json`. The overclaim was replaced with "runs in-process on the server with no model calls".
+- `tests/test_assistant_query.py`: 15 new regression tests (see Validation coverage).
+
+**Result.** The unit suite was run on the working copy and on the clean unpacked release. Both runs reported 362 tests, 357 passing, 5 skipped (Selenium-dependent) and 0 failures. The 15 new tests account for the change from 347 to 362.
+
+**Validation boundaries for this release.** Live Gemini calls require a valid key and external API access, which this environment does not provide, so the live path is validated through a fake transport. A browser run of the chat error path is a planned validation step. Both items are listed under Known technical limitations.
+
+## Defects found and corrected
+
+Each defect below has a regression test where the test suite can express it.
+
+**Phase 6 (security and reliability audit):**
+
+- Partial findings were saved after a failed batch. Saves now run in a single transaction with rollback.
 - A migration that failed midway could leave a stale schema version. Migrations now run in transactions and report errors.
-- Analyses interrupted by a restart stayed in `running`. They are marked `failed` with error code `interrupted` when the store opens.
+- Analyses interrupted by a restart remained in `running`. They are now marked `failed` with error code `interrupted` when the store opens.
 - Repository text reached Markdown unescaped. It is now escaped.
-- Provider tokens and PEM private-key bodies were not redacted. Rules SEC-SECRET-003 and SEC-SECRET-004 report them, and PEM bodies are removed from excerpts and AI prompts. A test checks that no raw token appears in findings, excerpts or prompts.
+- Provider tokens and PEM private-key bodies were not redacted. Rules `SEC-SECRET-003` and `SEC-SECRET-004` report them, and PEM bodies are removed from excerpts and AI prompts. A test checks that no raw token appears in findings, excerpts or prompts.
 - A non-string branch name crashed the handler. Input types are validated, and unexpected errors return a generic `500 internal_error` without internal detail.
-- Responses lacked security headers. They now carry a Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy` and `Permissions-Policy`. Streams carry the CSP too.
-- Concurrency was unbounded. A global cap of 8 running analyses (`NEXUS_MAX_ACTIVE_ANALYSES`) returns 503 `server_busy`, and a global cap of 64 event streams applies alongside the per-account limit of 8.
-- The sign-in failure table grew without bound. It is now bounded at 10,000 entries and pruned.
-- Expired sessions were never removed. They are pruned when a new session starts.
-- Frontend XSS was reviewed by reading every dynamic interpolation in `dash.js`. Every user- and repository-derived value is escaped or set through `textContent`. No XSS was confirmed.
-
-### Problems found while building and verifying (all fixed)
-
-- A stale `server.py` from an earlier session still held port 8000 and served code without the graph routes. The new process could not bind, so the browser run tested old code. It was caught by a 404 on the graph route. The fix was to stop every server process and restart before each run.
-- Playwright's string-based `wait_for_function` uses `eval`, which the new CSP blocks. The browser journeys failed for that reason, not because of the application. The journeys now poll with `page.evaluate` through `tools/_wait.py`, which the policy allows.
-- Repeated test runs used up the sign-up limit for the test address (10 per hour). The limiter is in memory, so the browser runs restart the server before each journey.
-- The first graph view focused on a finding with one relationship, so the diagram was almost empty. The default is now the finding with the most relationships, and the diagram uses the full width of the drawer.
-
-### Graph and report correctness
-
-- Every edge is built from evidence: a finding location, an import statement with its line, a group membership with that member's own line, or a directory containment. The Phase 6 tests check that every import edge names a real repository file and that every edge has an explanation and a known status.
-- Impact paths are `potential`, and each cites the import that creates the path. Informational findings create no impact paths.
-- SARIF was checked with the stdlib checker in `tests/sarif_check.py` and a JSON Schema subset (`tests/fixtures/sarif-2.1.0-subset.schema.json`) under jsonschema 4.26.0 in a separate environment. The fixture export validated with zero errors. The official OASIS schema could not be downloaded here: the egress proxy refused schemastore.org and docs.oasis-open.org. Full validation against that schema is therefore not done.
-
-### Results (run on the final working copy)
-
-- Unit tests: 285 of 285 pass under `python -m unittest discover -s tests` and `python -m unittest discover -v`. Phase 6 adds 44 tests in `tests/test_phase6.py`, including the per-account stream isolation test.
-- Browser journeys: investigation 20 of 20 (includes the graph); live 19; live insertion 15; auth 21; end-to-end 42; pages 23; motion 28. Each ran in headless Chromium against the restarted server, with no page errors.
-- Screenshots: the graph was reviewed at 1366 px and at 390 px. Other screens were not reviewed in this phase.
-- Benchmark (synthetic, `tools/benchmark.py --large-files 1500`): 19 cases, 25 true positives, 0 false positives, 0 false negatives, precision, recall and F1 all 1.0. The labels were written from the rule definitions, so this checks consistency only. It is not a measure of real-world accuracy. The 1,500-file generated tree took 4.44 s on this machine.
-
-### Not done or not verified
-
-- The official SARIF schema was not validated (see above).
-- Accepted limitations remain: signup reveals whether an email is registered (409 `email_taken`); rate limits and the sign-in throttle are in memory and reset on restart; the Secure cookie flag depends on `NEXUS_COOKIE_SECURE`; `style-src` allows inline styles; JS and TS imports are read line by line; Python files that fail to parse are skipped; secret excerpts show the first three characters.
-- The AI explanation has not been called against a live provider.
-- No human review is recorded. Nothing has been pushed to GitHub, and nothing has been submitted to Arena.
-
-## Homepage section transitions (final ZIP follow-up)
-
-What changed:
-- `home.js`: a new section module (`initSectionBoundaries`, with `cleanupSectionBoundaries` on teardown). Each section boundary gets a hairline whose progress is scroll-scrubbed. It reads all boundary positions in one pass, then writes only values that changed, so it stops when the page is still. A faint light rises behind each section once it enters (`sec-in`, from one IntersectionObserver).
-- `home.css`: a hairline (`::before`) and a light (`::after`) on the statement, section and footer boundaries, using transform and opacity only. The statement section gets `overflow-x: clip`, because its light is wider than a 390px screen. Reduced motion shows the final state immediately.
-- `index.html`: `home.css?v=3` and `home.js?v=3`, so browsers load the new files.
-- The sticky How-it-works column is not inside a transformed ancestor, so it still sticks.
-
-Checks and results (real Chromium, 1440px and 390px):
-- `tools/home_section_transitions_check.py`: 14 of 14 pass. The hairline is hidden before its boundary, passes through partial values, and reaches full width. Light entry is marked and settles. No animations run while idle. The sticky column pins at 120px. No console or CSP errors. Reduced motion shows full hairlines with no animations. No horizontal overflow at 390px.
-- `tools/home_transitions_check.py` (earlier checker): 12 of 12 pass. Its old-code names are now matched as whole tokens, so `handleScrollFrame` is not flagged, and its version expectation is v3.
-- `tools/home_motion_check.py`: step indicator reading positions 01 to 05, monotonic change, track fill 0 to 1, five markers, and sticky offset 120px all pass. The failures seen before were a checker artifact: it read positions during `scroll-behavior: smooth`. The checker now disables smooth scrolling on its test pages only; the product keeps it.
-- Unit suite: 315 tests, 5 skipped (optional selenium). The previous error came from a selenium import in `tests/test_browser_journey.py`; that test now skips cleanly when selenium is missing.
-
-Not done:
-- `tools/home_journey.py` needs selenium, which is not in `requirements.txt`, so it was not run.
-- Recession effects (sections shrinking as the next one arrives) were not added. They need a transform on the section, which would break the sticky How-it-works column.
-
-
-## Homepage: Repository Investigation Workspace and motion follow-up
-
-- Replaced the self-drawing "Relationships draw themselves" graph (section `#investigation`) with a three-panel workspace: explorer, code with highlighted finding and trace lines, and a findings panel with severity, confidence, evidence and a relationship trace. The sample repository `sample-shop-api` is labelled as illustrative and is not connected to any real analysis.
-- Removed the `inv-graph-*` markup and CSS. `tests/test_home_workspace.py` guards against their return.
-- Selection is handled by one delegated click listener in `home.js` (`initInvestigation` / `cleanupInvestigation`). It runs no backend calls. The default state is visible without JavaScript.
-- Narrow screens: the explorer becomes file chips, and the panels stack.
-- Bumped `home.css` and `home.js` to `?v=4`. Updated the checker and optional Selenium journey to match.
-- Verified in headless Chromium at 1440, 1280, 1024, 768, 390 and 360px: no document overflow, no console errors, selection updates the code and findings panels, no-JS default state visible, reduced motion leaves no running animations.
-- Not done in this pass: the full Apple-style rework of every section, the hero entrance and nav dropdown audit, logged-in state verification, and screen recordings.
-- Button feedback: primary and light CTAs lift and glow on hover; the press state wins over hover. `home.css?v=5`.
-- Verified in headless Chromium: hero entrance (words, lead, CTA, panel reach full opacity by about 2.6 s; the CTA responds to clicks at 100 ms); nav hover-intent open, switch between triggers without closing (no flicker), Escape, keyboard Enter, outside click; logged-out header (Sign in, Get Started); logged-in header after sign-up through the real form (account menu, Escape closes it); the session cookie is HttpOnly and no token is stored in localStorage; mobile 390px menu opens without overflow; reduced motion shows final states with no running animations.
-
-## Pass: security rule coverage and held-out check
-
-- Gap found: the rule set (18 rules) had no detection for command injection, cross-site scripting sinks, unsafe deserialization or path traversal, although the brief lists them. Added `SEC-CMD-001`, `SEC-XSS-001`, `SEC-DESER-001` and `SEC-PATH-001`. All four are `potential` evidence: they match a sink on one line with non-literal input from the same line. Multi-line data flow is not traced.
-- False positive fixed: `SEC-EVAL-001` treated a destructured JavaScript `exec(` (from `child_process`) as dynamic eval. JavaScript now matches only `eval(`. Python still matches `eval` and `exec`.
-- Held-out check: six small cases in `tests/fixtures/heldout` were written before the new rules existed, with labels set from the file contents. `tools/heldout_benchmark.py` scores each case on the rule ids it labels: 6 true positives, 0 false positives, 0 false negatives (precision, recall and F1 all 1.0). This is author-written and small. It does not show real-world accuracy.
-- Unlabelled hits: across all rules, the same run has 7 false positives, all from rules not labelled in those cases: `LOOSE-MOD-001` (standalone files are not imported by any other file; this is a low-confidence heuristic and fires on entry-point scripts) and `GAP-VALID-001`. Overall precision across all rules on that corpus is 0.46. `LOOSE-MOD-001` is not tuned in this pass.
-- Probe: 16 snippet variants (including JavaScript `require("child_process").exec(...)` and pathlib `base / request.args[...]`) were used to find two misses, which were then fixed. These probes were used for tuning, so they are regression checks, not held-out results. They are in `tests/test_code_rules.py`.
-- Existing synthetic corpus after the change: 19 cases, 25 true positives, 0 false positives, 0 false negatives (unchanged).
-- Unit tests after the change: 326 run, OK, 5 skipped (322 before, plus 4 in `tests/test_code_rules.py`).
-- Not done in this pass: a fresh independently written evaluation set, multi-line taint tracking, languages beyond Python and JavaScript/TypeScript for the new rules, SSRF and weak-cryptography rules, and SARIF schema validation.
-
-## Pass: data flow, SSRF, weak cryptography, calibration
-
-- Added `nexus/flow.py`: sequential single-file taint tracking. Request input enters through common web frameworks (Python, JavaScript), Go net/http, Java servlets and PHP superglobals. It flows through assignments and concatenation, and a sanitizer or literal reassignment clears it. Findings name the line where the input entered. It does not cross files, object fields, function parameters or aliases.
-- Command injection (`SEC-CMD-001`) now covers Go (`sh -c`), Java (`Runtime.exec`) and PHP (`system` family), and uses taint for Python and JavaScript sinks. Path traversal (`SEC-PATH-001`) uses taint for file-access sinks in Python, JavaScript, Go, Java and PHP.
-- New rules: `SEC-SSRF-001` (outbound URL from request input, potential, medium) and `SEC-CRYPTO-001` (MD5, SHA-1, DES, RC4, ECB; potential, low).
-- Calibration after real-world review: `SEC-CRYPTO-001` lowered to low, and `SEC-DESER-001` lowered to medium, after all six of their hits on third-party packages were judged not to be vulnerabilities in context (see `benchmarks/realworld_review.json`). Single-author review; not independent.
-- False positives: runnable scripts (shebang or `__main__` guard) are exempt from `LOOSE-MOD-001`. `GAP-VALID-001` recognises common validation libraries (express-validator, celebrate, class-validator, ajv, wtforms, cerberus, Pydantic `BaseModel`, Zod, Joi). I tried a further change to `LOOSE-MOD-001`: skip repositories with no internal imports. It removed four labelled detections in the existing corpus, so I reverted it. The remaining noise on standalone files is documented, not suppressed.
-- JavaScript `exec` handling: a `child_process` call routed through `require(...).exec` is detected. A RegExp `.exec(` call is not.
-- Held-out sets: set 1 (6 cases) was used to tune the first four rules, so it is no longer independent. Set 2 (10 cases) was written before the rules and then used for regression, so it is not independent either. Labelled-scope results: set 1 6/6, set 2 10/10. All-rule false positives on set 2 are 14, all from unlabelled rules (`GAP-ERR-001` and `LOOSE-MOD-001`).
-- Existing synthetic corpus: 19 cases, 25 true positives, 0 false positives, 0 false negatives.
-- Unit tests: 347 run, OK, 5 skipped (includes 7 new tests in `tests/test_untrusted_repository.py` for symlinks, oversized and binary files, invalid encodings, nested archives, file-count limits and prompt-injection text, and 18 in `tests/test_code_rules.py` and `tests/test_flow.py`).
-- Not done: an independently written labelled evaluation set from real repositories; multi-line data flow across files; SSRF and weak-crypto rules for languages beyond those listed; official SARIF 2.1.0 schema validation (the official schema could not be obtained offline; `tests/sarif_check.py` checks a hand-encoded required-field subset and is not official validation); precision estimates for command injection, XSS, path traversal and deserialization on real code (zero hits on the sampled packages, so precision is unmeasured).
+- Responses lacked security headers. They now include a Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy` and `Permissions-Policy`. Event streams carry the CSP too.
+- Concurrency was unbounded. A global cap of 8 running analyses returns 503 `server_busy`, and a global cap of 64 event streams applies alongside the per-account limit of 8.
+- The sign-in failure table grew without bound. It is now capped at 10,000 entries and pruned.
+- Expired sessions were never removed. They are now pruned when a new session starts.
+
+**Phase 6 build and verification problems (all corrected):**
+
+- A stale `server.py` from an earlier session still held port 8000 and served code without the graph routes. A 404 on the graph route revealed it. Every server process is now stopped and restarted before each run.
+- Playwright's string-based `wait_for_function` uses `eval`, which the CSP blocks. The journeys now poll with `page.evaluate` through `tools/_wait.py`.
+- Repeated test runs exhausted the in-memory sign-up limit for the test address. Browser runs now restart the server before each journey.
+- The first graph view focused on a finding with one relationship, so the diagram was nearly empty. The default is now the finding with the most relationships, and the diagram uses the full drawer width.
+
+**Testing and browser fixes (earlier phases):**
+
+- The sign-out message was consumed by a `hashchange` event before the reload. It is now set with `history.replaceState`.
+- The browser suite could not reach the Reports exports after the buttons stopped being links. Each URL is now stored in `data-href`.
+- `GET /api/auth/me` returned 401 for signed-out visitors, logging a console error on every page load. It now returns 200 with `user: null`.
+- The dead-link check did not recognise router routes. Fixed.
+- The API reference overflowed a 390px viewport because long example JSON stretched a grid track. Fixed with `minmax(0, 1fr)`.
+- Some documentation statements did not match the rule definitions (slop description, gap and loose-end rules, risk score, a Python version claim). They were corrected against `nexus/rules.py` and `nexus/scanner.py`.
+- The API reference lists error codes, and a test fails if a listed code does not exist in the source.
+- Live-update tests counted heartbeat frames as analysis events. The test reader now excludes heartbeats, and a separate test checks the heartbeat itself.
+- The event stream was missing from the API reference. It was added, and a route test checks streaming routes against `server.py`.
+- Chromium does not close an open `EventSource` when the browser goes offline, so the page did not notice a dropped stream. The page now closes the stream on the offline event, reconnects on online, and has a watchdog for silent connections.
+- A stream refused with HTTP 429 left the page in "reconnecting" indefinitely, because `EventSource` does not retry refused streams. A scheduled retry now runs while the session is valid.
+- A superseded View Transition rejected an internal promise, which surfaced as a page error. Those promises are now handled.
+
+**Motion polish pass:**
+
+- The success path of finding open passed its wiring callback as the drawer source, and the status-select listener ran before the deferred body rendered. The callback now runs once the body is rendered.
+- The drawer close button sat under the detail header on project drawers, so clicks on it were intercepted. It now has a stacking level above the body.
+
+**Phase 5 and later:**
+
+- The `SEC-EVAL-001` false positive on JavaScript `exec` was corrected (see the security rule coverage pass).
+- The `LOOSE-MOD-001` change that removed labelled detections was reverted (see the data flow pass).
+- The `tests/test_browser_journey.py` Selenium import error was resolved, so the test skips cleanly when Selenium is absent.
+
+## Validation coverage
+
+### Unit test progression
+
+| Milestone | Tests run | Result | Notes |
+| --- | --- | --- | --- |
+| Baseline, Phases 1 to 4 (Arena commands in a clean copy) | 163 | OK | Also run with `-W error::ResourceWarning`; server health check on port 8000 answered |
+| Motion polish pass | 163 | OK | Motion, live insertion and other browser journeys recorded in that pass |
+| Phase 5 | 241 | OK | Includes scope-policy regression tests |
+| Phase 6 | 285 | OK | Phase 6 added 44 tests |
+| Homepage section transitions | 315 | OK, 5 skipped | Skips are the optional Selenium tests |
+| Security rule coverage pass | 326 | OK, 5 skipped | Includes 4 tests in `tests/test_code_rules.py` |
+| Data flow, SSRF, cryptography and calibration pass | 347 | OK, 5 skipped | Adds untrusted-repository, flow and rule tests |
+| v4 Nexus AI assistant fix (current) | 362 | OK, 5 skipped, 0 failures | Adds 15 tests in `tests/test_assistant_query.py`; 357 passed |
+
+The v4 totals were reproduced on the working copy and on the clean unpacked release. Run the suite with `python -m unittest discover -s tests -p "test_*.py"`.
+
+The v4 regression module covers:
+
+- The API error shape the frontend reads, and a static check that `assistant.js` reads `error.message`.
+- Missing credentials, which fall back without a warning.
+- Invalid credentials (`ai_auth_failed`), provider timeout (`ai_timeout`), rate limit (`ai_rate_limited`), unexpected exceptions (`ai_unexpected_error`), malformed provider output, and empty provider output (`ai_empty_response`). Each falls back with a notice naming its code, and no key appears in logs.
+- A valid Gemini response through a fake transport, with the key sent only in the `x-goog-api-key` header and the model named in the URL.
+- HTTP 403 and 429 responses from the transport, mapped to their error codes.
+- The two suggested questions, answered from code constants and the benchmark file, and the no-measurement case.
+
+### Browser journeys
+
+Browser journeys were last recorded before v4, in headless Chromium at 1366px and 390px. Results were as follows: live 19 of 19; auth 21 of 21; end-to-end 42 of 42; pages 23 of 23; motion 28 of 28; live insertion 15 of 15; investigation 20 of 20. Screenshots were reviewed for the graph and the homepage only. A browser run of the v4 chat error path is a planned validation step.
+
+### Benchmarks and held-out results
+
+Benchmark claims are reported with their labelling source and limits:
+
+| Measure | Result | Source and caveat |
+| --- | --- | --- |
+| Synthetic labelled corpus | 19 cases; 25 TP, 0 FP, 0 FN; precision, recall, F1 = 1.0 | Labels were written from the rule definitions, so this checks consistency, not real-world accuracy |
+| Held-out set 1 | 6 of 6 on labelled rules | Used to tune the first four rules; no longer independent |
+| Held-out set 2 | 10 of 10 on labelled rules; 14 false positives from unlabelled rules | Written before the rules, then used for regression; no longer independent |
+| Large generated tree | 1,500 files in 3.32 s (`benchmarks/results.json`); earlier runs 4.23 s and 4.44 s | Single machine; timing depends on hardware, file sizes and languages |
+| External repositories | flask 3, itsdangerous 2, is-number 1 findings | Unlabelled; counts only, no precision claim |
+| Real-world review of third-party hits | Six hits from `SEC-CRYPTO-001` and `SEC-DESER-001` judged not vulnerabilities in context | Single-author review, not independent |
+
+Per-rule precision for command injection, cross-site scripting, path traversal and deserialization on real code is unmeasured. The sampled packages produced zero hits for those rules.
+
+### Known single-run anomalies
+
+- One live insertion run recorded 14 of 15 with one failure. Its name was not captured, and it did not reproduce on rerun. It is treated as unexplained.
+- Motion and live insertion journeys timed out during sign-up after repeated runs exhausted the in-memory sign-up rate limit. A server restart cleared it.
+
+## Known technical limitations
+
+These are the current scope and validation boundaries of the product.
+
+- **Live AI provider.** Live-provider integration requires valid provider credentials and an environment with external API access. Automated provider and fallback behaviour is covered by regression tests that use a fake transport, so the live path has not been exercised in this environment.
+- **Browser verification of the latest chat fix.** The v4 change to the chat error path is covered by unit and API-level tests. A browser run of that path is a planned validation step.
+- **Language coverage.** AST analysis covers Python only. JavaScript and TypeScript use pattern rules and relative-import resolution, not a parser. Python files that fail to parse are skipped.
+- **Cross-file data flow.** Taint tracking operates within one file. It does not follow data across files, through function parameters, object fields or aliases. Cross-file correlation is limited to the deterministic grouping and the broken-import rule.
+- **Detection false positives.** `LOOSE-MOD-001` is a low-confidence heuristic that fires on standalone entry-point scripts; runnable scripts are exempt, but other standalone files are reported. `GAP-VALID-001` and `GAP-ERR-002` produce known false positives, for example on a swallowed exception with an explanatory comment. Test, example and documentation paths are excluded from some heuristics by design, which means real issues in those paths are not reported by them. Security (`SEC-*`) rules apply everywhere.
+- **Rule breadth.** `SEC-SSRF-001` and `SEC-CRYPTO-001` cover the languages listed in the data flow pass. Their coverage in other languages is outside current scope.
+- **SARIF schema validation.** SARIF 2.1.0 output was checked against a hand-encoded required-field subset and a JSON Schema subset. The official OASIS schema could not be retrieved from this environment, so output has not been validated against the official schema.
+- **Benchmark scope.** The labelled corpus was written from the rule definitions. The held-out sets are small and are no longer independent. No independently labelled evaluation set from real repositories exists yet.
+- **Timing and interface measurements.** Benchmark timings come from one machine. Island width per state, main-thread cost during transitions and real-device behaviour have not been measured. Screenshots of screens other than the graph and the homepage have not been reviewed.
+- **Homepage test tooling.** `tools/home_journey.py` requires Selenium, which is not listed in `requirements.txt`. Selenium-dependent tests skip when it is absent (5 skipped in the current suite).
+- **Feature scope.** Repository ingestion supports public GitHub repositories and ZIP uploads. Private repository access is outside the current scope. Sign-in uses email and password; OAuth, email verification and password reset are outside the current scope. Profile settings (display name, password change) are outside the current scope.
+- **Live updates.** Findings are saved when an analysis completes, so they arrive in one batch. Live insertion is verified through the completed-analysis path.
+
+## Security and reliability considerations
+
+**Authentication and sessions**
+
+- Passwords use PBKDF2-SHA256 with a random salt and `hmac.compare_digest`. A dummy hash is computed for unknown email addresses so that response timing does not reveal accounts.
+- Session tokens are random 32-byte values, stored as SHA-256 digests. Sign-out and expiry are enforced on the server. Forged and expired cookies are rejected by tests.
+- Sign-up returns 409 `email_taken` for registered addresses. This reveals whether an address is registered, which is a known trade-off of the current flow.
+
+**Authorization and isolation**
+
+- Analysis, finding, project and report routes check the owner. Other users receive 404. Tests cover read, write and export access.
+- Event streams are authorized per account, hold one server thread each, are capped at 8 per account and 64 globally, and carry only whitelisted fields.
+
+**Request protection**
+
+- State changes use JSON bodies and a cookie set with SameSite=Lax. Cross-site form posts do not carry the cookie.
+- A cookie-authenticated POST or PATCH whose `Origin` differs from `Host` is refused with 403. There is no token-based CSRF defence. Requests without an `Origin` header bypass this check.
+- No CORS headers are sent.
+- Rate limits for sign-up, sign-in, analysis starts and inspection requests are applied per address or per account, with `Retry-After`. Limits are held in memory and reset on restart. The limiter uses the socket address, and no trusted-proxy policy exists.
+
+**Error handling and disclosure**
+
+- API errors use a fixed `{code, message}` shape. Unexpected server exceptions are logged on the server and not sent to the client.
+- AI provider failures are logged with an error code and HTTP status only. API keys, prompt contents and provider response bodies are not written to logs.
+- Secret excerpts show the first three characters of the value.
+
+**Browser and transport**
+
+- Responses carry a Content-Security-Policy and related headers. `style-src` permits inline styles.
+- The Secure cookie flag depends on `NEXUS_COOKIE_SECURE`. Behaviour behind a TLS-terminating proxy has not been tested.
+- Frontend interpolation was reviewed by reading every dynamic value in `dash.js`. User- and repository-derived values are escaped or set through `textContent`. No cross-site scripting issue was confirmed in that review.
+
+**Dependencies**
+
+- The Python application has no third-party runtime dependencies beyond the standard library. The frontend uses no npm packages in the page. A formal dependency review has not been performed.
+
+**Reliability**
+
+- Analyses have a 60-second time budget and scan at most 5,000 files. A reached limit marks the result as partial.
+- The assistant's AI call has a 25-second timeout. Failures return the deterministic answer with a notice.
+- Migrations run in transactions. Interrupted analyses are marked on startup.
+
+## Review and Quality Assurance
+
+**Automated testing.** The suite runs 362 unit tests (357 passing, 5 skipped for optional Selenium, 0 failing) on the v4 release, and includes security, ingestion, rules, flow analysis, AI provider and fallback behaviour, API contracts, SARIF checks and documentation consistency. Browser journeys in headless Chromium cover sign-up, live updates, the dashboard, public pages, motion and the homepage. Automated checks and their results are recorded in Validation coverage.
+
+**AI-assisted implementation.** The application code, tests and documentation were produced by Claude, an AI coding assistant. The v4 assistant fix was produced in the same way, under a brief to diagnose before changing code.
+
+**AI-assisted inspection.** The AI assistant performed code reading and inspection as part of the development process. Examples include the Phase 6 frontend interpolation review, the Phase 6 security and reliability audit across Phases 1 to 5, and the v4 root-cause diagnosis, which traced the error shape through the API and frontend and the provider failure through the backend. These are AI-assisted reviews. They are not independent audits.
+
+**Real-world calibration.** Six third-party hits were reviewed by the assistant and judged not to be vulnerabilities in context. This was a single-author review (see `benchmarks/realworld_review.json`).
+
+**Policy decisions during external review.** Phase 5 scope decisions are recorded as having been made during an external review pass. This log does not identify the reviewer, the review scope or the review dates.
+
+**Manual human review.** No manual review by a named human reviewer, and no review date or sign-off, is recorded in this log. The review status of this project is therefore limited to the automated checks and AI-assisted inspection described above.
+
+**Independent security audit.** No independent security audit has been commissioned or recorded. The Phase 6 audit was an internal audit conducted by the AI assistant.
+
+**Certification.** No certification, approval or third-party attestation is claimed or recorded.
+
+**Scope and dates of review activity.** Review activity is recorded where dates are known. The v4 unit suite and clean-copy checks were run on 2026-11-18. Dates for the earlier phases are absent from the project files. The benchmark and browser runs listed above are associated with the phase in which they were run.
+
+## Future development scope
+
+These are planned areas, separate from the current scope above.
+
+- **Live provider verification.** Run the Gemini path with a valid key in an environment with external API access, and record the results. Repeat the check for the Anthropic and OpenAI providers if they are enabled.
+- **Browser verification of the chat fix.** Run the chat error path in the browser and record the result.
+- **Official SARIF validation.** Validate exports against the official OASIS 2.1.0 schema where it can be retrieved.
+- **Independent evaluation.** Build a labelled evaluation set from real repositories written independently of the rules, and report precision for each rule family.
+- **Analysis depth.** Extend taint tracking across function parameters and files; extend SSRF and cryptography coverage to additional languages; add parser-based analysis for JavaScript and TypeScript.
+- **Heuristic tuning.** Tune `LOOSE-MOD-001` and `GAP-VALID-001` against the unlabelled hits recorded above.
+- **Security hardening.** Add a trusted-proxy policy for client addresses, an optional CSRF token, and tests for the Secure cookie behind a TLS proxy.
+- **Interface work.** Consider a recession effect for homepage sections, once it can be added without affecting the sticky column; add an interactive API playground; redesign onboarding beyond the Analyze page.
+- **Accounts.** Profile settings (display name and password change), OAuth sign-in, email verification and password reset.
+- **Repository scope.** Private repository support.
+- **Measurement.** Measure island width, main-thread cost during transitions and real-device motion; review screenshots of all screens at 1366px and 390px.
+- **Test tooling.** Add Selenium to optional test requirements, and investigate the single unexplained live insertion failure.
+- **Hosting.** Publish a release that has passed the live provider check and the browser check above.
+
+## Record corrections since v3
+
+This log supersedes several earlier statements. Where the original text said otherwise, the current record is as follows:
 
+- The rule count is 24, not 18. The four security rules added in the security rule coverage pass and the two added in the data flow pass make up the difference.
+- Earlier roadmap entries for a global event-stream cap and for per-finding live insertion are superseded. The global cap of 64 streams was added in Phase 6. Live insertion was verified by `tools/live_insertion_journey.py`, which passed 15 of 15 checks.
+- The earlier statement that the AI explanation had no live-provider run remains accurate for the explanation path. It now also applies to the chat fix in v4.
+- The earlier statement that the project had no recorded review remains accurate. The review record is now described in Review and Quality Assurance.
