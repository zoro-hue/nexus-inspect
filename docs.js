/* Public documentation (#docs/<section>) and API reference (#api/<endpoint>). Public pages, no session needed.
   The API reference renders the live catalog from GET /api/reference; the rule list renders from GET /api/rules. */
(() => {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (s) => document.querySelector(s);

  /* Documentation content. Each statement describes behaviour implemented in the server or the dashboard. */
  const SECTIONS = [
    { slug: 'introduction', group: 'Start', title: 'Introduction', html: `
      <p>NEXUS INSPECT investigates a code repository and reports what it finds, with evidence. It looks at four things: security weaknesses, AI-generated implementation slop (placeholders, stubs and code that does not match its name), coding gaps (missing validation, error handling or tests), and loose ends (unfinished TODOs, unused dependencies and modules that nothing imports).</p>
      <p>Every finding names a rule, a file and a line range, quotes a redacted excerpt, explains the impact and gives a remediation and steps to verify the fix. NEXUS does not claim that a finding is a proven vulnerability. Confidence is stated separately from severity.</p>
      <p>The analysis is deterministic: the same repository and the same rule set produce the same findings. An optional AI layer can add a contextual explanation to a single finding, but it never creates findings and is off unless you configure it.</p>` },
    { slug: 'quickstart', group: 'Start', title: 'Quickstart', html: `
      <ol>
        <li>Start the server: <code>python server.py</code>, then open <code>http://127.0.0.1:8000</code>.</li>
        <li>Create an account from <a href="#signup">Sign up</a>. Passwords need at least 10 characters, with one letter and one number.</li>
        <li>Open <a href="#analyze">Analyze</a>. Enter a public GitHub repository URL and check it, or upload a ZIP archive of a repository.</li>
        <li>Start the analysis and watch the stages. When it completes, open the findings.</li>
        <li>Open a finding to read its evidence, change its status, or export a report from <a href="#reports">Reports</a>.</li>
      </ol>` },
    { slug: 'installation', group: 'Start', title: 'Installation', html: `
      <p>NEXUS INSPECT uses only the Python 3 standard library. There are no third-party packages to install.</p>
      <pre><code>python -m pip install -r requirements.txt   # installs nothing; the file documents this
python -m unittest discover -v           # runs the test suite
python server.py                          # serves the site and API on port 8000</code></pre>
      <p>The project has been tested with Python 3.13. The database is created automatically at <code>data/nexus.sqlite</code> the first time the server starts.</p>` },
    { slug: 'configuration', group: 'Start', title: 'Configuration', html: `
      <p>Configuration is read from environment variables when the server starts.</p>
      <table class="doc-table"><thead><tr><th>Variable</th><th>Default</th><th>Purpose</th></tr></thead><tbody>
        <tr><td><code>PORT</code></td><td>8000</td><td>Port the server listens on.</td></tr>
        <tr><td><code>HOST</code></td><td>0.0.0.0</td><td>Interface to bind. Use 127.0.0.1 to keep the server local.</td></tr>
        <tr><td><code>NEXUS_DB</code></td><td>data/nexus.sqlite</td><td>SQLite database file.</td></tr>
        <tr><td><code>NEXUS_COOKIE_SECURE</code></td><td>unset</td><td>Set to 1 when serving over HTTPS so the session cookie is sent only on secure connections.</td></tr>
        <tr><td><code>NEXUS_AI_API_KEY</code></td><td>unset</td><td>Enables the optional AI explanation. Without it, AI features report that they are disabled.</td></tr>
        <tr><td><code>NEXUS_AI_MODEL</code></td><td>claude-haiku-5-5</td><td>Model used for AI explanations.</td></tr>
      </tbody></table>` },
    { slug: 'import', group: 'Use', title: 'Repository import', html: `
      <p>You can analyse a repository in two ways.</p>
      <ul>
        <li><strong>GitHub URL.</strong> Only <code>https://github.com/owner/repo</code> addresses are accepted. The URL is checked and its branches are listed before anything is downloaded. Redirects are followed only when they stay on GitHub's allowed hosts. Private repositories are not supported because NEXUS does not accept access tokens.</li>
        <li><strong>ZIP upload.</strong> Upload an archive of up to 25 MB. Archives with path traversal, symbolic links, excessive compression ratios or too many entries are rejected before analysis starts.</li>
      </ul>` },
    { slug: 'workflow', group: 'Use', title: 'Analysis workflow', html: `
      <p>An analysis moves through these stages. The dashboard shows the stage the server has actually reached; it does not estimate a percentage.</p>
      <ol>
        <li><strong>Queued</strong>: the analysis record exists and is waiting to run.</li>
        <li><strong>Retrieving repository</strong>: the archive is downloaded or read from the upload.</li>
        <li><strong>Indexing files</strong>: supported files are listed with their language and line count.</li>
        <li><strong>Inferring architecture</strong>: manifests and entry points are identified.</li>
        <li><strong>Running rules</strong>: the rules run over each file, and repository-level checks run once. Python files are also parsed, so AST checks can report stubs, swallowed exceptions and unreachable code.</li>
        <li><strong>Correlating and scoring</strong>: related findings are linked and each finding receives a risk score.</li>
        <li><strong>Preparing report</strong>: the findings are saved and the analysis is marked completed.</li>
      </ol>
      <p>A cancelled analysis stops at the next stage boundary. If the analysis reaches its time budget, it completes as a partial result and says so on the analysis record.</p>` },
    { slug: 'security', group: 'Detection', title: 'Security detection', html: `
      <p>Security rules cover six patterns: a hard-coded credential in source or configuration (SEC-SECRET-001), a real-looking secret in a committed env file (SEC-SECRET-002), SQL built by string concatenation (SEC-SQL-001), dynamic code execution with a non-literal argument (SEC-EVAL-001), debug mode enabled in configuration (SEC-DEBUG-001), and a sensitive route registered without an authorization guard (SEC-AUTHZ-001). Secret values are masked in every excerpt and export: the first three characters are kept and the rest is replaced.</p>` },
    { slug: 'slop', group: 'Detection', title: 'Slop detection', html: `
      <p>The slop rule (SLOP-STUB-001) finds functions that return a hard-coded success value without performing the operation their name describes. It identifies a pattern, not authorship: a finding means the code needs human review, and NEXUS does not determine who or what wrote it.</p>` },
    { slug: 'gaps', group: 'Detection', title: 'Coding gaps', html: `
      <p>Two gap rules are implemented. GAP-VALID-001 flags request input used without a visible validation step. GAP-ERR-001 flags a network call without visible error handling. NEXUS does not measure test coverage and does not run your test suite.</p>` },
    { slug: 'loose', group: 'Detection', title: 'Loose ends', html: `
      <p>Three loose-end rules are implemented: a TODO or FIXME marker left in code (LOOSE-TODO-001), a declared dependency that is never imported (LOOSE-DEP-001), and a module that is never imported (LOOSE-MOD-001). These are the easiest findings to triage and the most likely to be intentional.</p>` },
    { slug: 'explanations', group: 'Understand', title: 'Finding explanations', html: `
      <p>Each finding shows its rule, the file and lines, a redacted excerpt with the relevant line highlighted, the explanation, the impact, the remediation and verification steps. Related findings in the same file or of the same rule are listed with their status.</p>
      <p>The optional <em>Explain with AI</em> action sends a redacted copy of the excerpt to the configured provider and checks the reply: it must cite only the file and lines it was given. Without a configured key the action is unavailable and nothing is generated.</p>` },
    { slug: 'severity', group: 'Understand', title: 'Severity and confidence', html: `
      <p><strong>Severity</strong> (critical, high, medium, low) describes the impact if the issue is real. It is set by the rule and does not change per file.</p>
      <p><strong>Confidence</strong> (0 to 1) describes how sure the rule is that the pattern is real. A high-severity finding can have low confidence. Use the confidence filter to hide weaker matches.</p>
      <p>The risk score is severity weight multiplied by confidence, plus 2 for each other finding in the same file, capped at 100. The findings list sorts by this score by default.</p>` },
    { slug: 'triage', group: 'Understand', title: 'Triage and status', html: `
      <p>Each finding has one status: open, confirmed, fixed, false positive or suppressed. Marking a finding as a false positive or suppressing it requires a reason. Reopening a finding returns it to open. Every change is kept in the finding's history.</p>` },
    { slug: 'exports', group: 'Use', title: 'Report exports', html: `
      <p>Reports are available for completed analyses in three formats:</p>
      <ul>
        <li><strong>JSON</strong>: the analysis, summary counts and every finding with its evidence, excerpt and triage history.</li>
        <li><strong>Markdown</strong>: a readable report with severity totals and a finding list.</li>
        <li><strong>SARIF 2.1.0</strong>: for code-scanning tools that read the standard format.</li>
      </ul>
      <p>The download buttons are enabled only when a completed analysis is selected.</p>` },
    { slug: 'privacy', group: 'Trust', title: 'Security and privacy', html: `
      <p>Accounts are stored with passwords hashed using PBKDF2-SHA256 and a random salt. Session tokens are stored only as digests, and cookies are HttpOnly with SameSite=Lax. Sessions last seven days and end when you sign out.</p>
      <p>Each account sees only its own projects and analyses. Another account's analysis is reported as not found.</p>
      <p>Repository contents are processed on the server. Secret values are redacted before they reach the database, the API, the dashboard or an export. Repository archives are unpacked into a temporary directory that is removed when the analysis ends.</p>` },
    { slug: 'supported', group: 'Trust', title: 'Supported functionality', html: `
      <p>Supported today: account sign-up and sign-in, projects, GitHub URL and ZIP analysis, cancellation, the four categories, finding filters, search and sorting, status changes with history, related findings, JSON, Markdown and SARIF exports, analysis history, project rename, and the API reference.</p>
      <p>Not supported: private repositories, OAuth sign-in, email verification, password reset, project deletion, and team sharing.</p>` },
    { slug: 'limitations', group: 'Trust', title: 'Limitations', html: `
      <p>Detection is rule-based. It will miss issues that no rule describes, and it will report some patterns that are harmless in context. Treat findings as leads to verify, not as a verdict.</p>
      <ul>
        <li>The login throttle is kept in memory and resets when the server restarts.</li>
        <li>The server serves plain HTTP unless you place it behind HTTPS yourself.</li>
        <li>Large repositories are cut off by the time budget and marked partial.</li>
        <li>Rule accuracy has been measured only on the labelled fixtures in the repository, which were written for this project.</li>
      </ul>` },
    { slug: 'rules', group: 'Reference', title: 'Rule catalog', dynamic: 'rules', html: '' },
  ];

  const ENDPOINT_GROUP_ORDER = ['Service', 'Authentication', 'Projects', 'Analyses', 'Findings', 'Reports'];
  const epId = (ep) => `${ep.method}-${ep.path}`.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  let endpoints = null;
  let rules = null;

  async function getJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Request failed with HTTP ${res.status}.`);
    return res.json();
  }

  /* ---------- Documentation ---------- */
  function renderDocsShell(slug, query) {
    const toc = $('#docsToc');
    const q = query.trim().toLowerCase();
    const groups = {};
    SECTIONS.forEach((s) => { (groups[s.group] = groups[s.group] || []).push(s); });
    toc.innerHTML = Object.keys(groups).map((g) => {
      const items = groups[g].filter((s) => !q || s.title.toLowerCase().includes(q) || s.html.toLowerCase().includes(q));
      if (!items.length) return '';
      return `<p class="doc-group">${esc(g)}</p>` + items.map((s) =>
        `<a class="doc-link${s.slug === slug ? ' on' : ''}" href="#docs/${s.slug}"${s.slug === slug ? ' aria-current="page"' : ''}>${esc(s.title)}</a>`).join('');
    }).join('') || '<p class="muted doc-empty">No section matches “' + esc(query) + '”.</p>';
  }

  async function renderDocSection(slug) {
    const section = SECTIONS.find((s) => s.slug === slug) || SECTIONS[0];
    const body = $('#docsBody');
    body.classList.remove('doc-in'); void body.offsetWidth; body.classList.add('doc-in');
    const index = SECTIONS.indexOf(section);
    const prev = SECTIONS[index - 1];
    const next = SECTIONS[index + 1];
    let html = section.html;
    if (section.dynamic === 'rules') {
      try {
        rules = rules || (await getJSON('/api/rules')).items;
        html = `<p>The rules, loaded from the running server. Each rule's severity and confidence are fixed in the rule definition.</p>` +
          rules.map((r) => `<div class="doc-rule"><div><span class="tag">${esc(r.category)}</span> <code>${esc(r.id)}</code></div>
            <h3 class="doc-h3">${esc(r.title)}</h3>
            <p>${esc(r.impact)}</p>
            <p class="muted">Severity ${esc(r.severity)} · confidence ${esc(String(r.confidence))} · method: ${esc(r.method)}</p></div>`).join('');
      } catch (err) {
        html = `<p class="field-error" role="alert">The rule list could not be loaded: ${esc(err.message)}</p>`;
      }
    }
    body.innerHTML = `
      <p class="doc-kicker mono">${esc(section.group)}</p>
      <h1 class="doc-title">${esc(section.title)}</h1>
      ${html}
      <nav class="doc-pager" aria-label="Section navigation">
        ${prev ? `<a class="btn btn-doc-pager" href="#docs/${prev.slug}">← ${esc(prev.title)}</a>` : `<button class="btn btn-doc-pager" disabled aria-disabled="true">← Previous</button>`}
        ${next ? `<a class="btn btn-doc-pager" href="#docs/${next.slug}">${esc(next.title)} →</a>` : `<button class="btn btn-doc-pager" disabled aria-disabled="true">Next →</button>`}
      </nav>`;
    document.title = `${section.title} — NEXUS INSPECT docs`;
  }

  /* ---------- API reference ---------- */
  function codeBlock(text, label) {
    return `<div class="api-code"><pre><code>${esc(text)}</code></pre><button class="btn btn-ghost btn-sm" type="button" data-copy="${esc(text)}" aria-label="Copy ${esc(label)} example">Copy</button></div>`;
  }

  function renderEndpoint(ep) {
    const params = ep.params.length
      ? `<h3 class="doc-h3">Parameters</h3><table class="doc-table"><thead><tr><th>Name</th><th>In</th><th>Description</th></tr></thead><tbody>${ep.params.map((p) =>
          `<tr><td><code>${esc(p.name)}</code></td><td>${esc(p.in)}</td><td>${esc(p.description)}</td></tr>`).join('')}</tbody></table>` : '';
    let body = '';
    if (ep.body) {
      body = typeof ep.body === 'string'
        ? `<h3 class="doc-h3">Request body</h3><p>${esc(ep.body)}</p>`
        : `<h3 class="doc-h3">Request body (JSON)</h3><table class="doc-table"><thead><tr><th>Field</th><th>Description</th></tr></thead><tbody>${Object.entries(ep.body).map(([k, v]) =>
            `<tr><td><code>${esc(k)}</code></td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>`;
    }
    const responses = ep.responses.map((r) => `
      <div class="api-response"><span class="api-status s${String(r.status)[0]}">${r.status}</span>
        <div><p>${esc(r.description)}</p>${codeBlock(JSON.stringify(r.example, null, 2), `${r.status} response`)}</div></div>`).join('');
    return `
      <article class="api-ep" id="${epId(ep)}" tabindex="-1">
        <div class="api-head"><span class="method m-${ep.method.toLowerCase()}">${esc(ep.method)}</span><code class="api-path">${esc(ep.path)}</code></div>
        <p>${esc(ep.summary)}</p>
        <p class="muted"><strong>Authentication:</strong> ${esc(ep.auth)}</p>
        ${params}${body}
        <h3 class="doc-h3">Responses</h3>${responses}
        <h3 class="doc-h3">Example</h3>${codeBlock(ep.example, ep.method + ' ' + ep.path)}
      </article>`;
  }

  function renderApiShell(slug, query) {
    const q = query.trim().toLowerCase();
    const matches = endpoints.filter((ep) => !q || `${ep.method} ${ep.path} ${ep.summary} ${ep.group}`.toLowerCase().includes(q));
    const toc = $('#docsToc');
    toc.innerHTML = ENDPOINT_GROUP_ORDER.map((g) => {
      const items = matches.filter((ep) => ep.group === g);
      if (!items.length) return '';
      return `<p class="doc-group">${esc(g)}</p>` + items.map((ep) =>
        `<a class="doc-link api-toc-link" href="#api/${epId(ep)}"><span class="method m-${ep.method.toLowerCase()}">${esc(ep.method)}</span> <span class="mono">${esc(ep.path)}</span></a>`).join('');
    }).join('') || '<p class="muted doc-empty">No endpoint matches.</p>';

    const body = $('#docsBody');
    body.classList.remove('doc-in'); void body.offsetWidth; body.classList.add('doc-in');
    body.innerHTML = `
      <p class="doc-kicker mono">Reference</p>
      <h1 class="doc-title">API reference</h1>
      <p>Every endpoint below is served by the running server and is checked by the test suite. Responses are JSON unless stated otherwise. Errors use the shape <code>{"error": {"code": "…", "message": "…"}}</code>. Protected endpoints need the session cookie from signing in; examples use <code>jar.txt</code> for that cookie.</p>
      <p class="muted">The reference is also available as JSON at <a href="/api/reference">/api/reference</a>.</p>
      ${matches.map(renderEndpoint).join('') || '<p class="muted">No endpoint matches this search.</p>'}`;
    document.title = 'API reference — NEXUS INSPECT';
    if (slug) {
      const target = document.getElementById(slug);
      if (target) target.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }
  }

  /* ---------- Page entry ---------- */
  let current = { base: null, slug: null };
  let ready = null;

  function loadApi() {
    if (endpoints) return Promise.resolve();
    if (!ready) ready = getJSON('/api/reference').then((d) => { endpoints = d.items; });
    return ready;
  }

  async function show(base, slug) {
    const search = $('#docsSearch');
    const query = search.value || '';
    current = { base, slug };
    $('#docsPage').dataset.kind = base;
    $('#docsKicker').textContent = base === 'api' ? 'API reference' : 'Documentation';
    try {
      if (base === 'api') {
        await loadApi();
        renderApiShell(slug, query);
      } else {
        renderDocsShell(slug || 'introduction', query);
        await renderDocSection(slug || 'introduction');
      }
    } catch (err) {
      $('#docsBody').innerHTML = `<p class="field-error" role="alert">${esc(err.message)}</p>`;
    }
  }

  function bind() {
    $('#docsSearch').addEventListener('input', () => {
      const { base, slug } = current;
      if (!base) return;
      if (base === 'api') renderApiShell(slug, $('#docsSearch').value);
      else renderDocsShell(slug || 'introduction', $('#docsSearch').value);
    });
    $('#docsBody').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-copy]');
      if (!btn) return;
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = 'Copied';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
      } catch (_) {
        btn.textContent = 'Copy unavailable';
      }
    });
  }

  bind();
  window.NexusDocs = { show };
})();
