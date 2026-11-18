/* Dashboard: every panel reads from and writes to the NEXUS INSPECT API. No demo data is used here. */
(() => {
  const API = '/api';
  const SEV = ['critical', 'high', 'medium', 'low'];
  const CATS = { security: 'Security', slop: 'AI slop', gap: 'Coding gaps', loose: 'Loose ends' };
  const STATUS = { open: 'Open', confirmed: 'Confirmed', false_positive: 'False positive', suppressed: 'Suppressed', fixed: 'Fixed' };
  const REASON_REQUIRED = new Set(['false_positive', 'suppressed']);
  const STAGES = [
    ['queued', 'Queued'], ['ingestion', 'Retrieving repository'], ['file_indexing', 'Indexing files'],
    ['architecture', 'Inferring architecture'], ['security_and_quality', 'Running rules'],
    ['correlation', 'Correlating and scoring'], ['report_generation', 'Preparing report'],
  ];
  const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
  const RING = 2 * Math.PI * 52;

  const state = {
    view: null,
    filter: { sev: 'all', cat: 'all' },
    analysis: localStorage.getItem('nexus.analysis') || null,
    run: null,            // { id, timer } for the analysis being watched
    branchesFor: null,
    findingsTimer: null,
    health: null,
  };

  /* ---------- API client ---------- */
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts = Object.assign({}, opts, { body: JSON.stringify(opts.json) });
    }
    let res;
    try {
      res = await fetch(API + path, Object.assign({}, opts, { headers }));
    } catch (_) {
      throw Object.assign(new Error('The NEXUS server cannot be reached. Start it with python server.py.'), { code: 'offline' });
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (res.status === 401) window.dispatchEvent(new Event('nexus:unauthorized'));
    if (!res.ok) {
      const err = new Error((data && data.error && data.error.message) || `Request failed with HTTP ${res.status}.`);
      err.code = data && data.error ? data.error.code : 'http_' + res.status;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—');
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  const sevLabel = (s) => cap(s);
  const statusLabel = (s) => STATUS[s] || s;

  function setConnection(ok, msg) {
    const el = document.getElementById('connStatus');
    if (!el) return;
    el.classList.toggle('bad', !ok);
    el.querySelector('span').textContent = msg;
  }
  async function checkHealth() {
    try {
      const h = await fetch('/health', { cache: 'no-store' }).then((r) => r.json());
      setConnection(true, h.ok ? `Server online · v${h.version}` : 'Server responded unexpectedly');
    } catch (_) {
      setConnection(false, 'Server offline');
    }
  }

  /* ---------- Navigation ---------- */
  function moveIndicator(targetView = state.view) {
    const link = document.querySelector(`.capsule-link[data-view="${targetView}"]`);
    const ind = document.getElementById('capsuleInd');
    if (!link || !ind) return;
    ind.style.width = link.offsetWidth + 'px';
    ind.style.transform = `translateX(${link.offsetLeft}px)`;
  }

  function applyView(name) {
    if (!document.querySelector(`[data-view="${name}"].view`)) name = 'overview';
    state.view = name;
    document.querySelectorAll('.capsule-link').forEach((a) => {
      const on = a.dataset.view === name;
      a.classList.toggle('on', on);
      a.setAttribute('aria-current', on ? 'page' : 'false');
    });
    document.querySelectorAll('#dash .view').forEach((v) => {
      const on = v.dataset.view === name;
      v.classList.toggle('active', on);
      v.hidden = !on;
      if (on) { v.classList.remove('view-enter'); void v.offsetWidth; v.classList.add('view-enter'); }
    });
    moveIndicator(name);
    // Heading first, then metric groups and panels in document order. Lists stagger when their data arrives.
    const current = document.querySelector(`#dash .view[data-view="${name}"]`);
    stagger(current, '.page-title, .page-sub, .grid-4 > *, .panel:not(.list-panel)', 28, 6);
    if (name === 'overview') loadOverview();
    if (name === 'findings') loadFindingsPage();
    if (name === 'projects') loadProjects();
    if (name === 'reports') loadReports();
  }

  /* Route changes inside the dashboard are sequenced rather than cross-faded: the outgoing view fades and lifts out,
     then the incoming view arrives with its staggered entry. The header and capsule stay live, so the capsule glides
     as a normal transition. A View Transition was tried first and rejected: it captured the whole page, which
     double-exposed the two views and froze the staggered entries while the snapshot played. */
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let pendingSwitch = null;
  window.showView = function showView(name) {
    if (pendingSwitch) {
      clearTimeout(pendingSwitch.timer);
      if (pendingSwitch.anim) pendingSwitch.anim.cancel();
      pendingSwitch = null;
    }
    const outgoing = state.view ? document.querySelector(`#dash .view[data-view="${state.view}"]:not([hidden])`) : null;
    if (reducedMotion() || !outgoing || name === state.view) { applyView(name); return; }
    moveIndicator(name);
    document.querySelectorAll('.capsule-link').forEach((a) => {
      const on = a.dataset.view === name;
      a.classList.toggle('on', on);
      a.setAttribute('aria-current', on ? 'page' : 'false');
    });
    const anim = outgoing.animate(
      [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-6px)' }],
      { duration: 130, easing: 'cubic-bezier(.25,1,.5,1)', fill: 'forwards' });
    pendingSwitch = {
      anim,
      timer: setTimeout(() => { anim.cancel(); pendingSwitch = null; applyView(name); }, 130),
    };
  };

  window.closeAllPops = function closeAllPops() {};
  window.closeDrawer = closeDrawer;

  /* ---------- Overview ---------- */
  function healthScore(bySev) {
    const w = { critical: 15, high: 8, medium: 3, low: 1 };
    const penalty = SEV.reduce((sum, s) => sum + (bySev[s] || 0) * w[s], 0);
    return Math.max(0, 100 - penalty);
  }

  function renderSeverity(bySev, total) {
    const host = document.getElementById('ovSev');
    if (!total) { host.innerHTML = '<p class="muted">No findings in the latest completed analysis.</p>'; return; }
    host.innerHTML = SEV.map((s) => {
      const n = bySev[s] || 0;
      const pct = total ? Math.round((n / total) * 100) : 0;
      return `<div class="sev-row"><span class="sev ${s}">${sevLabel(s)}</span>
        <div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div><b class="mono">${n}</b></div>`;
    }).join('');
  }

  function renderCategories(byCat) {
    document.getElementById('ovCats').innerHTML = Object.keys(CATS).map((c) =>
      `<button class="cat-tile" data-go-findings="${c}"><span class="stat-label">${CATS[c]}</span><span class="stat-num small">${byCat[c] || 0}</span></button>`
    ).join('');
  }

  async function loadOverview() {
    try {
      const [projects, analyses] = await Promise.all([api('/projects'), api('/analyses')]);
      const completed = analyses.items.filter((a) => a.status === 'completed');
      tweenNumber('ovRepos', projects.items.length);
      tweenNumber('ovAnalyses', analyses.items.length);
      setText('ovAnalysesFoot', completed.length ? `${completed.length} completed` : 'none completed yet');
      const latest = completed[0];
      if (!latest) {
        setText('ovFindings', 0);
        setText('ovFindingsFoot', 'run an analysis to see results');
        renderSeverity({}, 0);
        renderCategories({});
        setText('ovHealthNum', '—');
        document.getElementById('ringVal').style.strokeDashoffset = RING;
      } else {
        const res = await api(`/analyses/${latest.id}/findings`);
        const total = res.summary.total;
        tweenNumber('ovFindings', total);
        setText('ovFindingsFoot', `from ${latest.projectName || 'latest analysis'}`);
        renderSeverity(res.summary.bySeverity, total);
        renderCategories(res.summary.byCategory);
        const h = healthScore(res.summary.bySeverity);
        tweenNumber('ovHealthNum', h);
        document.getElementById('ringVal').style.strokeDashoffset = RING * (1 - h / 100);
      }
      const recent = analyses.items.slice(0, 5);
      document.getElementById('ovRecent').innerHTML = recent.length
        ? recent.map((a) => `<button class="list-row" data-open-analysis="${esc(a.id)}">
            <span>${esc(a.projectName || a.id)}</span><span class="mono muted">${esc(a.status)} · ${fmtDate(a.createdAt)}</span></button>`).join('')
        : '<p class="muted">No analyses yet. Start one from Analyze.</p>';
      stagger(document.getElementById('ovRecent'), '.list-row', 44, 6);
      setText('ovSub', completed.length ? 'Results from the latest completed analysis, with stored history.' : 'No completed analyses yet.');
    } catch (err) {
      setText('ovSub', err.message);
    }
  }

  /* ---------- Analyze ---------- */
  function renderStages(current, status) {
    const idx = STAGES.findIndex(([k]) => k === current);
    const list = document.getElementById('stages');
    list.innerHTML = STAGES.map(([key, label], i) => {
      let cls = 'pending';
      if (status === 'completed') cls = 'done';
      else if (i < idx) cls = 'done';
      else if (i === idx) cls = status === 'failed' || status === 'cancelled' ? 'stopped' : 'active';
      return `<li class="stage ${cls}"><span class="dot-stage" aria-hidden="true"></span><span>${esc(label)}</span></li>`;
    }).join('');
  }

  function setRunUI(analysis) {
    const bar = document.getElementById('runProgress');
    bar.style.width = (analysis.progress || 0) + '%';
    const label = STAGES.find(([k]) => k === analysis.stage);
    const statusText = {
      queued: 'Queued on the server.', running: `${label ? label[1] : 'Working'}… ${analysis.progress || 0}%`,
      completed: 'Analysis complete.', failed: 'Analysis failed.', cancelled: 'Analysis cancelled.',
    }[analysis.status] || analysis.status;
    setText('runStatus', statusText);
    renderStages(analysis.stage, analysis.status);
    const cancel = document.getElementById('cancelBtn');
    cancel.hidden = !(analysis.status === 'queued' || analysis.status === 'running');
  }

  function stopWatching() {
    if (state.run && state.run.timer) clearTimeout(state.run.timer);
    state.run = null;
  }

  /* The server's events drive the run; REST is still read on every update, so the UI always shows persisted state.
     While the live stream is open, the timer is a slow safety resync; otherwise it polls quickly as the fallback. */
  function watchAnalysis(id) {
    stopWatching();
    const run = { id, timer: null, tick: null };
    state.run = run;
    document.getElementById('summary').hidden = true;
    document.getElementById('runError').hidden = true;
    const schedule = () => {
      if (!state.run || state.run.id !== id) return;
      clearTimeout(run.timer);
      run.timer = setTimeout(tick, window.NexusLive && NexusLive.isLive ? 6000 : 700);
    };
    const tick = async () => {
      if (!state.run || state.run.id !== id) return;
      let a;
      try { a = await api(`/analyses/${id}`); } catch (err) { setText('runStatus', err.message); schedule(); return; }
      if (!state.run || state.run.id !== id) return;
      setRunUI(a);
      if (TERMINAL.has(a.status)) {
        stopWatching();
        finishRun(a);
        return;
      }
      schedule();
    };
    run.tick = () => { clearTimeout(run.timer); tick(); };
    tick();
  }

  function finishRun(a) {
    state.analysis = a.id;
    localStorage.setItem('nexus.analysis', a.id);
    if (a.status === 'completed') {
      api(`/analyses/${a.id}/findings`).then((res) => {
        const s = res.summary.bySeverity;
        setText('sumCritical', s.critical); setText('sumHigh', s.high); setText('sumTotal', res.summary.total);
        setText('sumNote', `${a.filesScanned} files scanned${a.partial ? ` · partial result: ${a.partialReason}` : ''}. Results are stored and can be reviewed any time.`);
        document.getElementById('summary').hidden = false;
      });
      toast('Analysis complete');
    } else {
      const box = document.getElementById('runError');
      box.hidden = false;
      box.textContent = a.errorMessage || 'The analysis did not finish.';
    }
    document.getElementById('cancelBtn').hidden = true;
    document.getElementById('startBtn').disabled = false;
  }

  function setFieldError(id, msg) { setText(id, msg || ''); }

  let isCheckingRepo = false;
  const inspectCache = new Map();

  function applyInspectResult(info, url) {
    const sel = document.getElementById('branch');
    const branches = info.branches || [];
    const defaultBranch = info.defaultBranch || 'HEAD';
    sel.innerHTML = `<option value="">Default branch (${esc(defaultBranch)})</option>` +
      branches.filter((b) => b !== defaultBranch).map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join('');
    sel.disabled = false;
    setText('checkNote', `${info.owner}/${info.repo}: ${branches.length} branch${branches.length === 1 ? '' : 'es'} listed.`);
    state.branchesFor = url;
    document.getElementById('startBtn').disabled = false;
  }

  async function checkRepository() {
    if (isCheckingRepo) return;
    const url = document.getElementById('analyzeUrl').value.trim();
    setFieldError('analyzeErr', '');
    if (!url) { setFieldError('analyzeErr', 'Enter a GitHub repository URL first.'); return; }

    const cacheKey = url.toLowerCase();
    const cached = inspectCache.get(cacheKey);
    if (cached && cached.expires > Date.now()) {
      applyInspectResult(cached.data, url);
      return;
    }

    const btn = document.getElementById('checkBtn');
    btn.disabled = true;
    isCheckingRepo = true;
    setText('checkNote', 'Checking the repository on GitHub…');
    try {
      const info = await api('/repositories/inspect', { method: 'POST', json: { url } });
      inspectCache.set(cacheKey, { data: info, expires: Date.now() + 300000 });
      applyInspectResult(info, url);
    } catch (err) {
      setFieldError('analyzeErr', err.message);
      setText('checkNote', 'The repository could not be checked.');
    } finally {
      btn.disabled = false;
      isCheckingRepo = false;
    }
  }

  async function startFromUrl() {
    const url = document.getElementById('analyzeUrl').value.trim();
    const branch = document.getElementById('branch').value;
    setFieldError('analyzeErr', '');
    if (!url) { setFieldError('analyzeErr', 'Enter a GitHub repository URL.'); return; }
    return submitAnalysis(() => api('/analyses/url?async=1', { method: 'POST', json: { url, branch } }));
  }

  async function startFromZip() {
    const input = document.getElementById('zipFile');
    const file = input.files && input.files[0];
    setFieldError('uploadErr', '');
    if (!file) { setFieldError('uploadErr', 'Choose a ZIP archive first.'); return; }
    if (file.size > 25 * 1024 * 1024) { setFieldError('uploadErr', 'The archive is larger than the 25 MB limit.'); return; }
    const name = file.name.replace(/\.zip$/i, '').slice(0, 120) || 'uploaded-repository';
    return submitAnalysis(() => api(`/analyses?async=1&name=${encodeURIComponent(name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file,
    }));
  }

  async function submitAnalysis(send) {
    const btn = document.getElementById('startBtn');
    btn.disabled = true;
    try {
      const a = await send();
      toast('Analysis queued');
      watchAnalysis(a.id);
    } catch (err) {
      btn.disabled = false;
      const target = document.getElementById('analyzeUrl').value ? 'analyzeErr' : 'uploadErr';
      setFieldError(target, err.message);
    }
  }

  async function cancelRun() {
    if (!state.run) return;
    try {
      await api(`/analyses/${state.run.id}/cancel`, { method: 'POST' });
      toast('Cancellation requested. It takes effect at the next stage.');
    } catch (err) {
      toast(err.message);
    }
  }

  /* ---------- Findings ---------- */
  async function loadAnalysisPicker(selectId, selected) {
    const { items } = await api('/analyses');
    const done = items.filter((a) => a.status === 'completed');
    const sel = document.getElementById(selectId);
    sel.innerHTML = done.length
      ? done.map((a) => `<option value="${esc(a.id)}">${esc(a.projectName || a.id)} · ${fmtDate(a.createdAt)}</option>`).join('')
      : '<option value="">No completed analyses</option>';
    const pick = done.find((a) => a.id === selected) ? selected : (done[0] && done[0].id);
    if (pick) sel.value = pick;
    return pick || null;
  }

  async function loadFindingsPage() {
    const aid = await loadAnalysisPicker('analysisPick', state.analysis);
    state.analysis = aid;
    if (aid) localStorage.setItem('nexus.analysis', aid);
    await renderFindings();
  }

  function findingQuery() {
    const p = new URLSearchParams();
    const q = document.getElementById('findQ').value.trim();
    const status = document.getElementById('findStatus').value;
    if (q) p.set('q', q);
    if (status) p.set('status', status);
    if (state.filter.sev !== 'all') p.set('severity', state.filter.sev);
    if (state.filter.cat !== 'all') p.set('category', state.filter.cat);
    p.set('sort', 'risk');
    return p.toString();
  }

  async function renderFindings() {
    const list = document.getElementById('findList');
    const count = document.getElementById('findCount');
    if (!state.analysis) {
      list.innerHTML = '<div class="empty"><p>No completed analysis is selected.</p><a class="btn btn-light btn-sm" href="#analyze">Start an analysis</a></div>';
      count.textContent = '';
      setText('findSub', 'Choose a completed analysis to review its findings.');
      return;
    }
    try {
      const res = await api(`/analyses/${state.analysis}/findings?${findingQuery()}`);
      const analysis = await api(`/analyses/${state.analysis}`);
      setText('findSub', `${analysis.projectName} · ${res.summary.total} findings in total · ${analysis.filesScanned} files scanned`);
      count.textContent = `${res.items.length} shown`;
      if (!res.items.length) {
        if (!res.summary || res.summary.total === 0) {
          list.innerHTML = '<div class="empty clean-repo"><div style="font-size:24px;margin-bottom:8px">✓</div><p style="font-weight:600;margin:0 0 6px">Clean Repository</p><p class="muted">All deterministic checks completed with zero findings. The codebase adheres to the investigated security, quality, and structure checks.</p></div>';
        } else {
          list.innerHTML = '<div class="empty"><p>No findings match these filters.</p></div>';
        }
        findKey = null;
        return;
      }
      // A new analysis or filter is a full render with a stagger. A refresh of the same list is reconciled in place,
      // so rows keep their identity, new rows enter, and status changes are animated without moving other rows.
      const key = `${state.analysis}|${findingQuery()}`;
      const hasRows = list.querySelector('.finding:not(.leaving)');
      if (key !== findKey || !hasRows) {
        findKey = key;
        list.innerHTML = res.items.map(findingRow).join('');
        stagger(list, '.finding', 38, 12);
      } else {
        reconcileFindings(list, res.items);
      }
    } catch (err) {
      findKey = null;
      list.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
    }
  }

  let findKey = null;
  const findingMeta = (f) => `${f.filePath}:${f.startLine} · ${CATS[f.category] || f.category} · confidence ${Math.round(f.confidence * 100)}% · risk ${f.riskScore}`;
  function findingRow(f) {
    return `<button class="finding" data-fid="${esc(f.id)}" type="button">
      <span class="sev ${esc(f.severity)}">${sevLabel(f.severity)}</span>
      <span class="finding-main"><span class="finding-title">${esc(f.title)}</span>
        <span class="mono muted finding-meta">${esc(findingMeta(f))}</span></span>
      <span class="tag">${esc(statusLabel(f.status))}</span>
    </button>`;
  }
  function retrigger(el, cls) {
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }
  function reconcileFindings(list, items) {
    const rows = new Map();
    list.querySelectorAll('.finding:not(.leaving)').forEach((el) => rows.set(el.dataset.fid, el));
    const wanted = new Set(items.map((f) => f.id));
    rows.forEach((el, fid) => {
      if (wanted.has(fid)) return;
      rows.delete(fid);
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 260);
    });
    let prev = null;
    items.forEach((f) => {
      let el = rows.get(f.id);
      if (el) {
        const sev = el.querySelector('.sev');
        if (sev.className !== `sev ${f.severity}`) { sev.className = `sev ${f.severity}`; sev.textContent = sevLabel(f.severity); }
        const title = el.querySelector('.finding-title');
        if (title.textContent !== f.title) title.textContent = f.title;
        const meta = el.querySelector('.finding-meta');
        if (meta.textContent !== findingMeta(f)) meta.textContent = findingMeta(f);
        const tag = el.querySelector('.tag');
        const label = statusLabel(f.status);
        if (tag.textContent !== label) {
          tag.textContent = label;
          retrigger(tag, 'changed');
          retrigger(el, 'status-changed');
        }
      } else {
        const template = document.createElement('template');
        template.innerHTML = findingRow(f).trim();
        el = template.content.firstElementChild;
        el.classList.add('is-new');
        if (prev) prev.after(el); else list.prepend(el);
      }
      prev = el;
    });
  }

  /* Staggered entry: one index per element in document order, capped so long lists do not wait. */
  function stagger(root, selector, step = 40, max = 10) {
    if (!root) return;
    root.querySelectorAll(selector).forEach((el, i) => { el.style.animationDelay = Math.min(i, max) * step + 'ms'; });
  }

  /* ---------- Drawer ---------- */
  /* Expansion: the source card becomes the panel. A ghost panel grows from the card's box to the drawer's box, fading
     out as the real drawer content arrives. Closing runs the same motion in reverse, back into the card if it is still shown. */
  const kf = (r, extra) => Object.assign({ left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' }, extra);
  function drawerBox() {
    const w = Math.min(580, window.innerWidth);
    return { left: window.innerWidth - w, top: 0, width: w, height: window.innerHeight };
  }
  function makeGhost(box) {
    const ghost = document.createElement('div');
    ghost.className = 'fx-ghost';
    ghost.setAttribute('aria-hidden', 'true');
    Object.assign(ghost.style, { left: box.left + 'px', top: box.top + 'px', width: box.width + 'px', height: box.height + 'px' });
    document.body.appendChild(ghost);
    return ghost;
  }
  function expandGhost(el) {
    if (reducedMotion() || !el || !el.isConnected) return false;
    const from = el.getBoundingClientRect();
    if (!from.width) return false;
    const to = drawerBox();
    const ghost = makeGhost(from);
    const anim = ghost.animate([
      kf(from, { offset: 0, opacity: 1, borderRadius: '12px', background: '#151515' }),
      kf(to, { offset: 0.7, opacity: 1, borderRadius: '0px', background: '#0D0D0D' }),
      kf(to, { offset: 1, opacity: 0, borderRadius: '0px', background: '#0D0D0D' }),
    ], { duration: 340, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' });
    anim.onfinish = () => ghost.remove();
    return true;
  }
  function contractGhost(el) {
    if (reducedMotion() || !el || !el.isConnected) return;
    const to = el.getBoundingClientRect();
    if (!to.width) return;
    const from = drawerBox();
    const ghost = makeGhost(from);
    const anim = ghost.animate([
      kf(from, { offset: 0, opacity: 0, borderRadius: '0px', background: '#0D0D0D' }),
      kf(from, { offset: 0.3, opacity: 1, borderRadius: '0px', background: '#0D0D0D' }),
      kf(to, { offset: 1, opacity: 0, borderRadius: '12px', background: '#151515' }),
    ], { duration: 280, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' });
    anim.onfinish = () => ghost.remove();
  }
  const findingSource = (fid) => ({ kind: 'finding', find: () => document.querySelector(`.finding[data-fid="${CSS.escape(fid)}"]`) });
  const projectSource = (pid) => ({ kind: 'project', find: () => { const b = document.querySelector(`[data-project="${CSS.escape(pid)}"]`); return b ? b.closest('.project') : null; } });

  /* Shared elements: the row's severity badge, title, or project marker and name become the drawer's matching
     elements. A clone of each travels between them while the originals are held, so the text is never duplicated. */
  const SHARED = {
    finding: [['.sev', '.detail-head .sev'], ['.finding-title', '.detail-title']],
    project: [['.project-mark', '.detail-head .project-mark'], ['.project-name', '.detail-title']],
  };
  const FLIGHT_MS = 320;
  const FLIGHT_EASE = 'cubic-bezier(.2,.9,.25,1)';
  const drawerBody = () => document.getElementById('drawerBody');

  function flightPairs(source) {
    const row = source && source.find();
    const body = drawerBody();
    if (!row || !body || !SHARED[source.kind]) return [];
    return SHARED[source.kind].map(([rowSel, drawerSel]) => {
      const rowEl = row.querySelector(rowSel);
      const drawerEl = body.querySelector(drawerSel);
      return rowEl && drawerEl ? { rowEl, drawerEl } : null;
    }).filter(Boolean);
  }

  /* Clone `from`, animate the clone from its rect to `to`'s rect, and let the text size and font follow the destination. */
  function flyClone(from, to, duration = FLIGHT_MS) {
    const r0 = from.getBoundingClientRect();
    const r1 = to.getBoundingClientRect();
    if (!r0.width || !r1.width) return null;
    const cs0 = getComputedStyle(from);
    const cs1 = getComputedStyle(to);
    const clone = from.cloneNode(true);
    clone.removeAttribute('id');
    clone.classList.add('fx-shared');
    clone.setAttribute('aria-hidden', 'true');
    Object.assign(clone.style, {
      left: r0.left + 'px', top: r0.top + 'px', width: r0.width + 'px', height: r0.height + 'px',
      fontSize: cs0.fontSize, fontFamily: cs1.fontFamily, fontWeight: cs1.fontWeight, lineHeight: cs1.lineHeight,
    });
    document.body.appendChild(clone);
    const anim = clone.animate([
      { left: r0.left + 'px', top: r0.top + 'px', width: r0.width + 'px', height: r0.height + 'px', fontSize: cs0.fontSize },
      { left: r1.left + 'px', top: r1.top + 'px', width: r1.width + 'px', height: r1.height + 'px', fontSize: cs1.fontSize },
    ], { duration, easing: FLIGHT_EASE, fill: 'forwards' });
    anim.onfinish = () => clone.remove();
    return anim;
  }

  function markScrollable(root) {
    root.querySelectorAll('.excerpt').forEach((pre) => pre.classList.toggle('scrollable', pre.scrollWidth > pre.clientWidth + 1));
  }
  function setDrawerBody(html) {
    const body = drawerBody();
    body.innerHTML = html;
    [...body.children].forEach((el, i) => el.style.setProperty('--i', String(i)));
    markScrollable(body);
  }
  function flushPendingBody() {
    if (state.pendingBody == null) return;
    const html = state.pendingBody;
    const after = state.pendingAfter;
    state.pendingBody = null;
    state.pendingAfter = null;
    setDrawerBody(html);
    if (after) after();
  }

  function cleanFx() {
    clearTimeout(state.closeTimer);
    clearTimeout(state.pendingTimer);
    document.querySelectorAll('.fx-ghost, .fx-shared').forEach((el) => el.remove());
    document.querySelectorAll('.shared-lent').forEach((el) => el.classList.remove('shared-lent'));
    document.querySelectorAll('.shared-hold').forEach((el) => el.classList.remove('shared-hold'));
  }

  /* A card-sourced open keeps the panel transparent while the ghost grows from the card, then fades the panel in
     as the ghost lands. The shared elements fly during the same window. Other opens slide the panel in as before. */
  function openDrawer(html, source, after) {
    const drawer = document.getElementById('drawer');
    const wasOpen = drawer.classList.contains('open');
    cleanFx();
    drawer.classList.remove('closing', 'landing');
    drawer.style.transition = '';
    let ghosted = false;
    if (!wasOpen) {
      state.opener = document.activeElement;
      if (source && !reducedMotion()) {
        const el = source.find();
        ghosted = !!el && expandGhost(el);
      }
    }
    if (ghosted) {
      drawer.style.transition = 'none';
      drawer.classList.add('landing');
      state.flightEnd = performance.now() + FLIGHT_MS + 20;
    }
    if (source) state.drawerSource = source;
    if (wasOpen && state.flightEnd > performance.now()) {
      state.pendingBody = html;
      state.pendingAfter = after || null;
      clearTimeout(state.pendingTimer);
      state.pendingTimer = setTimeout(flushPendingBody, state.flightEnd - performance.now());
    } else {
      clearTimeout(state.pendingTimer);
      state.pendingBody = null;
      state.pendingAfter = null;
      setDrawerBody(html);
      if (after) after();
    }
    drawer.classList.add('open');
    document.getElementById('dim').classList.add('on');
    drawer.setAttribute('aria-hidden', 'false');
    if (ghosted) {
      void drawer.offsetWidth;
      drawer.style.transition = 'opacity .24s var(--settle) .18s';
      drawer.classList.remove('landing');
      setTimeout(() => { if (!drawer.classList.contains('landing')) drawer.style.transition = ''; }, 450);
      flightPairs(source).forEach(({ rowEl, drawerEl }) => {
        rowEl.classList.add('shared-lent');
        drawerEl.classList.add('shared-hold');
        const anim = flyClone(rowEl, drawerEl, FLIGHT_MS);
        const release = () => { drawerEl.classList.remove('shared-hold'); rowEl.classList.remove('shared-lent'); };
        if (anim) anim.finished.then(release, release); else release();
      });
    }
    drawer.focus({ preventScroll: true });
  }
  function closeDrawer() {
    const drawer = document.getElementById('drawer');
    if (!drawer.classList.contains('open') || drawer.classList.contains('closing')) return;
    const source = state.drawerSource;
    state.drawerSource = null;
    drawer.setAttribute('aria-hidden', 'true');
    document.getElementById('dim').classList.remove('on');
    const opener = state.opener;
    state.opener = null;
    if (opener && opener.isConnected && opener.focus) opener.focus({ preventScroll: true });
    if (reducedMotion()) {
      cleanFx();
      drawer.classList.remove('open', 'closing', 'landing');
      drawer.style.transition = '';
      return;
    }
    // The panel slides out and fades while the ghost and shared elements return to the row they came from.
    const el = source && source.find();
    if (el) {
      contractGhost(el);
      flightPairs(source).forEach(({ rowEl, drawerEl }) => {
        rowEl.classList.add('shared-lent');
        const anim = flyClone(drawerEl, rowEl, 260);
        const release = () => rowEl.classList.remove('shared-lent');
        if (anim) anim.finished.then(release, release); else release();
      });
    }
    drawer.classList.add('closing');
    drawer.style.transition = 'transform .28s cubic-bezier(.2,.9,.25,1), opacity .22s cubic-bezier(.16,1,.3,1)';
    state.closeTimer = setTimeout(() => {
      drawer.classList.remove('open', 'closing', 'landing');
      drawer.style.transition = '';
      cleanFx();
    }, 280);
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('drawer').classList.contains('open')) closeDrawer();
  });

  async function openFinding(aid, fid) {
    // The loading panel already carries the row's severity and title, so the shared elements have a destination.
    const row = document.querySelector(`.finding[data-fid="${CSS.escape(fid)}"]`);
    const rowSev = row && row.querySelector('.sev');
    const rowTitle = row && row.querySelector('.finding-title');
    const loading = rowSev && rowTitle
      ? `<div class="detail"><div class="detail-head"><span class="${esc(rowSev.className.replace(/^sev\s*/, 'sev '))}">${esc(rowSev.textContent)}</span></div><h2 class="detail-title">${esc(rowTitle.textContent)}</h2><p class="muted">Loading finding…</p></div>`
      : '<p class="muted">Loading finding…</p>';
    openDrawer(loading, findingSource(fid));
    try {
      const f = await api(`/analyses/${aid}/findings/${fid}`);
      const wireFindingDetail = () => {
        const sel = document.getElementById('newStatus');
        const syncReason = () => { document.getElementById('reasonWrap').hidden = !REASON_REQUIRED.has(sel.value); };
        sel.addEventListener('change', syncReason); syncReason();
        loadAIStatus();
      };
      const excerpt = (f.excerpt || []).map((row) =>
        `<span class="ln${row.highlight ? ' hl' : ''}"><span class="ln-no">${row.line}</span>${esc(row.text)}</span>`).join('\n');
      const related = (f.relatedDetails || []).map((r) =>
        `<button class="list-row" data-open-finding="${esc(r.id)}"><span class="sev ${r.severity}">${sevLabel(r.severity)}</span><span>${esc(r.title)}</span><span class="mono muted">${esc(r.filePath)}:${r.startLine}</span></button>`).join('')
        || '<p class="muted">No other findings in this file.</p>';
      const history = (f.history || []).map((h) =>
        `<li class="mono">${fmtDate(h.created_at)} · ${esc(statusLabel(h.from_status))} → ${esc(statusLabel(h.to_status))}${h.reason ? ` · “${esc(h.reason)}”` : ''}</li>`).join('')
        || '<li class="muted">No status changes yet.</li>';
      const options = ['open', 'confirmed', 'false_positive', 'suppressed', 'fixed'].filter((s) => s !== f.status)
        .map((s) => `<option value="${s}">${esc(STATUS[s])}</option>`).join('');
      openDrawer(`
        <div class="detail" data-aid="${esc(aid)}" data-fid="${esc(f.id)}">
          <div class="detail-head"><span class="sev ${f.severity}">${sevLabel(f.severity)}</span><span class="tag">${esc(statusLabel(f.status))}</span></div>
          <h2 class="detail-title">${esc(f.title)}</h2>
          <dl class="kv">
            <dt>Location</dt><dd class="mono">${esc(f.filePath)}:${f.startLine}${f.endLine && f.endLine !== f.startLine ? '–' + f.endLine : ''}</dd>
            <dt>Category</dt><dd>${esc(CATS[f.category] || f.category)}</dd>
            <dt>Rule</dt><dd class="mono">${esc(f.ruleId)}</dd>
            <dt>Confidence</dt><dd>${Math.round(f.confidence * 100)}%</dd>
            <dt>Risk score</dt><dd>${f.riskScore}</dd>
            <dt>Detection</dt><dd>${esc(f.detectionMethod)}</dd>
          </dl>
          <h3 class="detail-h">Evidence</h3>
          <pre class="code excerpt" aria-label="Source excerpt">${excerpt || esc(f.evidence)}</pre>
          <h3 class="detail-h">Why it matters</h3><p>${esc(f.explanation)}</p>
          <p class="muted">${esc(f.impact)}</p>
          <h3 class="detail-h">Recommended fix</h3><p>${esc(f.remediation)}</p>
          <h3 class="detail-h">How to verify</h3><ol class="steps">${(f.verificationSteps || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
          <h3 class="detail-h">Related findings in this file</h3><div class="related">${related}</div>
          <h3 class="detail-h">AI explanation (optional)</h3>
          <div class="ai-box" id="aiBox">
            <p class="muted" id="aiNote">Checking AI availability…</p>
            <button class="btn btn-ghost btn-sm" type="button" id="aiBtn" hidden>Explain with AI</button>
            <div id="aiResult"></div>
          </div>
          <h3 class="detail-h">Triage</h3>
          <form class="triage" id="triageForm" novalidate>
            <div class="field"><label for="newStatus">Change status</label>
              <select id="newStatus">${options}</select></div>
            <div class="field" id="reasonWrap" hidden><label for="reason">Reason (required)</label>
              <textarea id="reason" rows="3" placeholder="Explain why, for the audit trail"></textarea></div>
            <div class="field-error" id="triageErr" role="alert"></div>
            <button class="btn btn-primary btn-sm" type="submit">Apply status</button>
          </form>
          <h3 class="detail-h">History</h3><ul class="history">${history}</ul>
        </div>`, null, wireFindingDetail);
    } catch (err) {
      openDrawer(`<p class="field-error" role="alert">${esc(err.message)}</p>`);
    }
  }

  async function loadAIStatus() {
    const note = document.getElementById('aiNote');
    const btn = document.getElementById('aiBtn');
    if (!note) return;
    try {
      const info = await api('/ai/status');
      if (info.enabled) {
        note.innerHTML = `<strong>Live AI Available:</strong> Using ${esc(info.provider.toUpperCase())} (<code>${esc(info.model)}</code>). Explanations are generated from verified, redacted excerpts.`;
        btn.textContent = 'Explain with AI';
        btn.className = 'btn btn-primary btn-sm';
        btn.hidden = false;
        btn.dataset.aiMode = 'live';
      } else {
        note.innerHTML = `<strong>Deterministic Rule Engine:</strong> ${esc(info.reason)}<br><span style="font-size:11.5px;color:var(--muted);">${esc(info.setupInstructions || '')}</span>`;
        btn.textContent = 'Explain with Rule Knowledge';
        btn.className = 'btn btn-ghost btn-sm';
        btn.hidden = false;
        btn.dataset.aiMode = 'deterministic';
      }
    } catch (err) {
      note.textContent = err.message;
    }
  }

  async function explainWithAI(wrap, refresh = false) {
    const btn = document.getElementById('aiBtn');
    const out = document.getElementById('aiResult');
    const aid = wrap.dataset.aid, fid = wrap.dataset.fid;
    if (btn) btn.disabled = true;
    out.innerHTML = '<p class="muted">Generating context-aware explanation…</p>';
    try {
      let r;
      try {
        r = await api(`/analyses/${aid}/findings/${fid}/explain${refresh ? '?refresh=1' : ''}`, { method: 'POST' });
      } catch (err) {
        // If live AI is disabled or temporarily unavailable, gracefully fall back to deterministic explanation
        if (err.status === 503 || err.status === 502 || err.status === 504) {
          r = await api(`/analyses/${aid}/findings/${fid}/explain?fallback=1`, { method: 'POST' });
          r.temporarilyUnavailableNotice = `Live AI provider is currently offline (${err.message}). Showing deterministic rule engine explanation:`;
        } else {
          throw err;
        }
      }

      const metaTags = [];
      if (r.model) metaTags.push(`<span class="tag tag-ai">${esc(r.model)}</span>`);
      if (r.evidenceStatus) metaTags.push(`<span class="tag tag-${esc(r.evidenceStatus)}">${r.evidenceStatus === 'confirmed' ? 'Confirmed Evidence' : 'Potential Finding'}</span>`);
      if (r.falsePositiveRisk) metaTags.push(`<span class="tag">False-positive risk: ${esc(r.falsePositiveRisk)}</span>`);
      if (r.cached) metaTags.push('<span class="tag muted">Cached</span>');

      out.innerHTML = `
        <div class="ai-result">
          ${r.temporarilyUnavailableNotice ? `<p class="field-error" style="font-size:12px;margin-bottom:6px;">${esc(r.temporarilyUnavailableNotice)}</p>` : ''}
          <div class="ai-result-meta" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">
            ${metaTags.join('')}
          </div>
          <h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Issue Meaning</h4>
          <p>${esc(r.explanation)}</p>
          ${r.whyIdentified ? `<h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Detector Rationale</h4><p>${esc(r.whyIdentified)}</p>` : ''}
          ${r.realWorldImpact ? `<h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Real-World Impact</h4><p class="muted">${esc(r.realWorldImpact)}</p>` : ''}
          ${r.remediation ? `<h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Recommended Fix</h4><p>${esc(r.remediation)}</p>` : ''}
          ${r.correctedCode ? `<h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Corrected Code Example</h4><pre class="code excerpt"><code>${esc(r.correctedCode)}</code></pre>` : ''}
          <h4 class="ai-h" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 3px;">Verification Steps</h4>
          <ol class="steps">${(r.verificationSteps || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ol>
          <p class="muted ai-notice" style="font-size:11.5px;margin-top:8px;">${esc(r.notice)}</p>
          <div style="margin-top:10px;display:flex;gap:8px;">
            <button class="btn btn-ghost btn-sm" type="button" data-refresh-ai>Regenerate</button>
          </div>
        </div>`;

      const refBtn = out.querySelector('[data-refresh-ai]');
      if (refBtn) {
        refBtn.addEventListener('click', () => explainWithAI(wrap, true));
      }
      out.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      out.innerHTML = `
        <div class="ai-error-box" style="margin-top:8px;">
          <p class="field-error" role="alert">${esc(err.message)}</p>
          <button class="btn btn-ghost btn-sm" type="button" data-retry-ai style="margin-top:6px;">Retry</button>
        </div>`;
      const retryBtn = out.querySelector('[data-retry-ai]');
      if (retryBtn) {
        retryBtn.addEventListener('click', () => explainWithAI(wrap, true));
      }
      out.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function applyStatus(form) {
    const wrap = form.closest('.detail');
    const aid = wrap.dataset.aid, fid = wrap.dataset.fid;
    const status = document.getElementById('newStatus').value;
    const reason = document.getElementById('reason').value.trim();
    const err = document.getElementById('triageErr');
    if (REASON_REQUIRED.has(status) && !reason) { err.textContent = 'A reason is required for this status.'; return; }
    try {
      await api(`/analyses/${aid}/findings/${fid}`, { method: 'PATCH', json: { status, reason: reason || null } });
      toast(`Marked ${statusLabel(status).toLowerCase()}`);
      await openFinding(aid, fid);
      if (state.view === 'findings') renderFindings();
    } catch (e) {
      err.textContent = e.message;
    }
  }

  async function openAnalysis(aid) {
    openDrawer('<p class="muted">Loading analysis…</p>');
    try {
      const [a, files] = await Promise.all([api(`/analyses/${aid}`), api(`/analyses/${aid}/files`)]);
      const langs = Object.entries(a.languages || {}).map(([k, v]) => `${esc(k)} ${v}`).join(' · ') || '—';
      let investigationHtml = '';
      let graphHtml = '';
      if (a.status === 'completed') {
        try { investigationHtml = renderInvestigation(aid, await api(`/analyses/${aid}/investigation`)); }
        catch (err) { investigationHtml = `<h3 class="detail-h">Investigation</h3><p class="field-error" role="alert">${esc(err.message)}</p>`; }
        graphHtml = renderGraphShell(aid);
      }
      const rows = files.items.slice(0, 200).map((f) => `<li class="mono"><span>${esc(f.path)}</span><span class="muted">${esc(f.language)} · ${f.lines} lines</span></li>`).join('')
        || '<li class="muted">No files recorded.</li>';
      openDrawer(`
        <div class="detail">
          <div class="detail-head"><span class="tag">${esc(a.status)}</span><span class="mono muted">${esc(a.id)}</span></div>
          <h2 class="detail-title">${esc(a.projectName)}</h2>
          <dl class="kv">
            <dt>Source</dt><dd class="mono">${esc(a.projectSource)}</dd>
            <dt>Started</dt><dd>${fmtDate(a.createdAt)}</dd>
            <dt>Files scanned</dt><dd>${a.filesScanned}</dd>
            <dt>Languages</dt><dd>${langs}</dd>
            <dt>Partial result</dt><dd>${a.partial ? esc(a.partialReason) : 'No'}</dd>
            ${a.errorMessage ? `<dt>Error</dt><dd>${esc(a.errorMessage)}</dd>` : ''}
          </dl>
          <div class="row" style="margin:18px 0">
            ${a.status === 'completed' ? `<button class="btn btn-light btn-sm" data-findings-for="${esc(a.id)}" type="button">Open findings</button>
            <button class="btn btn-ghost btn-sm" data-report-for="${esc(a.id)}" type="button">Open report</button>` : ''}
          </div>
          <h3 class="detail-h">Files analyzed</h3>
          <ul class="file-list">${rows}</ul>
          ${investigationHtml}
          ${graphHtml}
        </div>`);
      if (graphHtml) loadGraph();
    } catch (err) {
      openDrawer(`<p class="field-error" role="alert">${esc(err.message)}</p>`);
    }
  }

  /* Deterministic investigation: every fact and group comes from the stored findings. Groups are hypotheses. */
  function renderInvestigation(aid, inv) {
    const r = inv.investigation;
    const counts = r.counts.byEvidenceClass;
    const facts = r.facts.map((f) => `<li class="inv-fact">${esc(f.text)}</li>`).join('');
    const groups = r.rootCauseGroups.map((g) => `
      <li class="inv-group">
        <p class="mono muted">${esc(g.id)} · ${esc(g.kind)} · ${esc(g.evidenceClass)}</p>
        <p>${esc(g.label)}</p>
        <p class="muted">Hypothesis: ${esc(g.hypothesis)}</p>
        <p class="mono muted">Files: ${g.files.map((f) => esc(f)).join(', ')}</p>
        <div data-group-result></div>
        ${inv.ai.enabled
          ? `<button class="btn btn-ghost btn-sm" type="button" data-explain-group="${esc(g.id)}" data-aid="${esc(aid)}">Explain with AI</button>`
          : `<p class="muted">${esc(inv.ai.reason)}</p>`}
      </li>`).join('') || '<li class="muted">No findings share a file or a pattern, so no groups were formed.</li>';
    return `
      <h3 class="detail-h">Investigation</h3>
      <p><span class="tag">Overall risk: ${esc(r.overallRisk)}</span></p>
      <p class="muted">${esc(r.methodology)}</p>
      <p class="mono">Confirmed ${counts.confirmed || 0} · Potential ${counts.potential || 0} · Informational ${counts.informational || 0}</p>
      <ul class="file-list">${facts}</ul>
      <h3 class="detail-h">Root-cause hypotheses</h3>
      <ul class="file-list">${groups}</ul>
      <p class="muted">${r.limitations.map((l) => esc(l)).join(' ')}</p>`;
  }


  /* Relationship graph. The server builds every node and edge from stored findings and import edges; each edge carries
     the evidence that supports it. The diagram shows one item and its direct relationships, and the list below it holds
     the same items for keyboard and screen-reader use. */
  const graphState = { aid: null, category: '', severity: '', evidenceClass: '', structure: false, focus: '', selected: '', last: null };
  const GRAPH_NEIGHBOUR_MAX = 12;
  const KIND_LABEL = { finding: 'Finding', file: 'File', group: 'Hypothesis group', component: 'Directory' };
  const KIND_ORDER = { finding: 0, group: 1, file: 2, component: 3 };

  function truncateLabel(text, max) {
    const t = String(text);
    return t.length > max ? t.slice(0, max - 1) + '…' : t;
  }

  function renderGraphShell(aid) {
    Object.assign(graphState, { aid, category: '', severity: '', evidenceClass: '', structure: false, focus: '', selected: '', last: null });
    const cats = Object.keys(CATS).map((c) => `<option value="${esc(c)}">${esc(CATS[c])}</option>`).join('');
    const sevs = ['critical', 'high', 'medium', 'low'].map((s) => `<option value="${s}">${esc(sevLabel(s))}</option>`).join('');
    return `
      <h3 class="detail-h" id="graphTitle">Relationship graph</h3>
      <p class="muted">Each relationship shows its evidence: a finding location, an import statement, or a grouping rule. Dashed lines are hypotheses that need review.</p>
      <form class="graph-controls" id="graphFilters" aria-label="Graph filters">
        <div class="field"><label for="gCat">Category</label><select id="gCat"><option value="">All categories</option>${cats}</select></div>
        <div class="field"><label for="gSev">Severity</label><select id="gSev"><option value="">All severities</option>${sevs}</select></div>
        <div class="field"><label for="gEv">Classification</label><select id="gEv"><option value="">All</option><option value="confirmed">Confirmed</option><option value="potential">Potential</option><option value="informational">Informational</option></select></div>
        <label class="check-row"><input type="checkbox" id="gStruct"> Show directory structure</label>
      </form>
      <div data-graph-body aria-live="polite" aria-labelledby="graphTitle"><p class="muted">Loading relationships…</p></div>`;
  }

  function graphParams() {
    const p = new URLSearchParams({ limit: '60', edgeLimit: '200' });
    if (graphState.category) p.set('category', graphState.category);
    if (graphState.severity) p.set('severity', graphState.severity);
    if (graphState.evidenceClass) p.set('evidenceClass', graphState.evidenceClass);
    if (graphState.structure) p.set('structure', '1');
    if (graphState.focus) p.set('focus', graphState.focus);
    return p;
  }

  async function loadGraph() {
    const body = document.querySelector('[data-graph-body]');
    if (!body || !graphState.aid) return;
    body.setAttribute('aria-busy', 'true');
    try {
      const g = await api(`/analyses/${graphState.aid}/graph?${graphParams()}`);
      if (!document.querySelector('[data-graph-body]')) return;
      graphState.last = g;
      body.innerHTML = renderGraphBody(g);
    } catch (err) {
      body.innerHTML = `<p class="field-error" role="alert">${esc(err.message)}</p>`;
    } finally {
      body.setAttribute('aria-busy', 'false');
    }
  }

  function renderGraphBody(g) {
    if (g.empty) {
      const note = g.notes[0] || 'No relationships are available for this analysis yet.';
      return `<div class="empty"><p>${esc(note)}</p>
        <p class="muted">Relationships appear when findings share a file, when files import one another, or when several findings share a pattern.</p></div>`;
    }
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    // Default focus: the finding with the most relationships, so the first view shows the most evidence.
    const degree = new Map();
    g.edges.forEach((e) => { degree.set(e.source, (degree.get(e.source) || 0) + 1); degree.set(e.target, (degree.get(e.target) || 0) + 1); });
    const fallback = [...g.nodes].filter((n) => n.kind === 'finding')
      .sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || (b.risk || 0) - (a.risk || 0))[0] || g.nodes[0];
    const focusId = graphState.focus && byId.has(graphState.focus) ? graphState.focus : fallback.id;
    if (!byId.has(graphState.selected)) graphState.selected = focusId;
    const selected = byId.get(graphState.selected) || byId.get(focusId);
    const stats = `<p class="mono muted">Showing ${g.stats.nodes} of ${g.stats.totalNodes} items · ${g.stats.edges} of ${g.stats.totalEdges} relationships${g.truncated ? ' · some items are not shown' : ''}</p>`;
    const notes = g.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join('');
    const back = graphState.focus ? '<p><button class="btn btn-ghost btn-sm" type="button" data-graph-clear>Show the whole graph</button></p>' : '';
    const list = [...g.nodes].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || (b.risk || 0) - (a.risk || 0))
      .map((n) => {
        const count = g.edges.filter((e) => e.source === n.id || e.target === n.id).length;
        const current = n.id === selected.id;
        return `<li><button class="graph-item${current ? ' is-current' : ''}" type="button" data-graph-select="${esc(n.id)}" aria-pressed="${current}">
          <span class="tag">${esc(KIND_LABEL[n.kind])}</span> <span>${esc(truncateLabel(n.label, 60))}</span>
          <span class="mono muted">${count} relationship${count === 1 ? '' : 's'}</span></button></li>`;
      }).join('');
    const legend = Object.entries(g.legend.statuses || {}).map(([k, v]) =>
      `<li><span class="g-swatch g-${esc(k)}" aria-hidden="true"></span> <strong>${esc(k)}</strong>: ${esc(v)}</li>`).join('');
    return `${stats}${notes}${back}
      <div class="graph-layout">
        <figure class="graph-figure">${graphSvg(g, focusId)}
          <figcaption class="muted">Diagram of ${esc(truncateLabel(selected.label, 60))} and its direct relationships. Press Tab to move through the items, and Enter to select one.</figcaption></figure>
        <div class="graph-side">
          <h4 class="detail-h">Items</h4>
          <ul class="graph-list" aria-label="Items in the graph">${list}</ul>
          <h4 class="detail-h">Selected item</h4>
          ${graphDetail(g, selected, byId)}
        </div>
      </div>
      <ul class="graph-legend" aria-label="Legend">${legend}</ul>`;
  }

  function graphSvg(g, centerId) {
    const center = g.nodes.find((n) => n.id === centerId);
    const linked = g.edges.filter((e) => e.source === centerId || e.target === centerId);
    const others = [];
    linked.forEach((e) => { const o = e.source === centerId ? e.target : e.source; if (!others.includes(o)) others.push(o); });
    const shown = others.slice(0, GRAPH_NEIGHBOUR_MAX);
    const W = 420, H = 340, cx = W / 2, cy = 150;
    const R = shown.length <= 4 ? 110 : 130;
    const pos = new Map();
    shown.forEach((id, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / shown.length;
      pos.set(id, [cx + R * Math.cos(a), cy + R * Math.sin(a)]);
    });
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    const lines = linked.filter((e) => pos.has(e.source === centerId ? e.target : e.source)).map((e) => {
      const [x, y] = pos.get(e.source === centerId ? e.target : e.source);
      return `<line class="g-edge g-${esc(e.status)}" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"><title>${esc(e.label)}: ${esc(e.explanation)}</title></line>`;
    }).join('');
    const node = (n, x, y, isCenter) => `
      <g class="g-node g-kind-${esc(n.kind)}${isCenter ? ' g-center' : ''}" role="button" tabindex="0" data-graph-select="${esc(n.id)}"
         aria-label="${esc(KIND_LABEL[n.kind])}: ${esc(n.label)}">
        <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${isCenter ? 26 : 18}"/>
        <text x="${x.toFixed(1)}" y="${(y + (isCenter ? 42 : 32)).toFixed(1)}" text-anchor="middle">${esc(truncateLabel(n.label, 26))}</text>
      </g>`;
    const nodes = shown.map((id) => node(byId.get(id), pos.get(id)[0], pos.get(id)[1], false)).join('');
    const hidden = others.length - shown.length;
    const summary = `${esc(KIND_LABEL[center.kind])} ${esc(center.label)} has ${others.length} direct relationship${others.length === 1 ? '' : 's'}${hidden > 0 ? `; ${hidden} more are in the list` : ''}.`;
    return `<svg class="graph-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${summary}" focusable="false">
      ${lines}${nodes}${node(center, cx, cy, true)}</svg>`;
  }

  function graphDetail(g, selected, byId) {
    const rel = g.edges.filter((e) => e.source === selected.id || e.target === selected.id);
    const items = rel.map((e) => {
      const other = byId.get(e.source === selected.id ? e.target : e.source);
      const evidence = e.evidence.slice(0, 5).map((x) =>
        `<li class="mono">${esc(x.path)}${x.line ? `:${x.line}` : ''}${x.text ? ` · <code>${esc(truncateLabel(x.text, 160))}</code>` : ''}</li>`).join('');
      return `<li class="graph-rel">
        <p><span class="tag g-tag-${esc(e.status)}">${esc(e.status)}</span> ${esc(e.label)}${other ? `: <button class="link-btn" type="button" data-graph-select="${esc(other.id)}">${esc(truncateLabel(other.label, 60))}</button>` : ''}</p>
        <p class="muted">${esc(e.explanation)}</p>
        <ul class="file-list">${evidence}</ul></li>`;
    }).join('');
    const actions = [];
    if (selected.kind === 'finding') actions.push(`<button class="btn btn-light btn-sm" type="button" data-graph-open-finding="${esc(selected.findingId)}">Open finding</button>`);
    if (selected.kind === 'file') actions.push(`<button class="btn btn-light btn-sm" type="button" data-graph-file-findings="${esc(selected.path)}">Findings in this file</button>`);
    if (selected.kind !== 'component') actions.push(`<button class="btn btn-ghost btn-sm" type="button" data-graph-focus="${esc(selected.id)}">Focus on this item</button>`);
    return `<p><strong>${esc(selected.label)}</strong></p>
      <p class="muted">${esc(KIND_LABEL[selected.kind])}${selected.severity ? ` · ${esc(selected.severity)}` : ''}${selected.evidenceClass ? ` · ${esc(selected.evidenceClass)}` : ''}</p>
      <div class="graph-actions">${actions.join('')}</div>
      ${rel.length ? `<ul class="graph-rels">${items}</ul>` : '<p class="muted">No relationships for this item in the current view.</p>'}`;
  }

  document.getElementById('drawerBody').addEventListener('change', (e) => {
    if (!e.target.closest('#graphFilters')) return;
    graphState.category = document.getElementById('gCat').value;
    graphState.severity = document.getElementById('gSev').value;
    graphState.evidenceClass = document.getElementById('gEv').value;
    graphState.structure = document.getElementById('gStruct').checked;
    loadGraph();
  });

  document.getElementById('drawerBody').addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.g-node')) {
      e.preventDefault();
      e.target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    }
  });

  document.getElementById('drawerBody').addEventListener('click', (e) => {
    const select = e.target.closest('[data-graph-select]');
    if (select && graphState.last) {
      graphState.selected = select.dataset.graphSelect;
      const body = document.querySelector('[data-graph-body]');
      if (body) body.innerHTML = renderGraphBody(graphState.last);
      return;
    }
    const focus = e.target.closest('[data-graph-focus]');
    if (focus) { graphState.focus = focus.dataset.graphFocus; graphState.selected = graphState.focus; loadGraph(); return; }
    if (e.target.closest('[data-graph-clear]')) { graphState.focus = ''; loadGraph(); return; }
    const open = e.target.closest('[data-graph-open-finding]');
    if (open) { openFinding(graphState.aid, open.dataset.graphOpenFinding); return; }
    const files = e.target.closest('[data-graph-file-findings]');
    if (files) {
      state.analysis = graphState.aid;
      localStorage.setItem('nexus.analysis', state.analysis);
      const q = document.getElementById('findQ');
      closeDrawer();
      q.value = files.dataset.graphFileFindings;
      location.hash = '#findings';
      q.dispatchEvent(new Event('input'));
    }
  });

  document.getElementById('drawerBody').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-explain-group]');
    if (!btn) return;
    const box = btn.closest('.inv-group').querySelector('[data-group-result]');
    btn.disabled = true;
    box.textContent = 'Asking the AI…';
    try {
      const r = await api(`/analyses/${btn.dataset.aid}/investigation/groups/${btn.dataset.explainGroup}/explain`, { method: 'POST' });
      box.innerHTML = `<p>${esc(r.explanation)}</p>
        <p class="muted">Hypothesis: ${esc(r.hypothesis)} · confidence ${esc(r.confidence)}</p>
        <ol class="steps">${r.verificationSteps.map((st) => `<li>${esc(st)}</li>`).join('')}</ol>
        <p class="muted">${esc(r.notice)}</p>`;
    } catch (err) {
      box.innerHTML = `<p class="field-error" role="alert">${esc(err.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- Projects ---------- */
  async function loadProjects() {
    const q = document.getElementById('projQ').value.trim().toLowerCase();
    const list = document.getElementById('projList');
    try {
      const { items } = await api('/projects');
      const shown = items.filter((p) => !q || (p.name + ' ' + p.source).toLowerCase().includes(q));
      setText('projCount', `${shown.length} of ${items.length} projects`);
      list.innerHTML = shown.length ? shown.map((p) => `
        <div class="project">
          <div class="project-cell"><span class="project-mark" aria-hidden="true">${esc(projectMark(p.name))}</span><div><div class="project-name">${esc(p.name)}</div><div class="mono muted">${esc(p.source)}</div></div></div>
          <div class="mono">${p.analysisCount} analyses</div>
          <div class="mono muted">${p.lastAnalysis ? fmtDate(p.lastAnalysis) : 'never analyzed'}</div>
          <button class="btn btn-ghost btn-sm" data-project="${esc(p.id)}" type="button">History</button>
        </div>`).join('') : '<div class="empty"><p>No projects yet. Import a repository from Analyze.</p></div>';
      stagger(list, '.project', 44, 10);
    } catch (err) {
      list.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
    }
  }

  const projectMark = (name) => (name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '#';
  async function openProject(pid) {
    const row = document.querySelector(`[data-project="${CSS.escape(pid)}"]`)?.closest('.project');
    const rowName = row && row.querySelector('.project-name');
    const loading = rowName
      ? `<div class="detail"><div class="detail-head"><span class="project-mark">${esc(projectMark(rowName.textContent))}</span></div><h2 class="detail-title">${esc(rowName.textContent)}</h2><p class="muted">Loading project…</p></div>`
      : '<p class="muted">Loading project…</p>';
    openDrawer(loading, projectSource(pid));
    try {
      const project = await api(`/projects/${encodeURIComponent(pid)}`);
      const completedAnalyses = (project.analyses || []).filter((x) => x.status === 'completed');
      const rows = project.analyses.map((a) => {
        const older = completedAnalyses.find((x) => x.id !== a.id && new Date(x.createdAt) < new Date(a.createdAt));
        const diffBtn = (a.status === 'completed' && older)
          ? `<button class="btn btn-ghost btn-sm" data-diff-curr="${esc(a.id)}" data-diff-base="${esc(older.id)}" type="button" title="Compare with earlier scan">Compare</button>`
          : '';
        return `
        <div class="history-row">
          <span class="tag">${esc(a.status)}</span>
          <span class="mono muted">${fmtDate(a.createdAt)}</span>
          <span>${a.status === 'completed' ? `${a.filesScanned} files` : esc(a.errorMessage || a.stage)}</span>
          <div style="display:flex;gap:4px">
            ${diffBtn}
            <button class="btn btn-ghost btn-sm" data-open-analysis="${esc(a.id)}" type="button">Details</button>
          </div>
        </div>`;
      }).join('') || '<p class="muted">No analyses for this project yet.</p>';
      openDrawer(`<div class="detail">
        <div class="detail-head"><span class="project-mark">${esc(projectMark(project.name))}</span></div>
        <h2 class="detail-title">${esc(project.name)}</h2>
        <p class="mono muted">${esc(project.source)}</p>
        <h3 class="doc-h3">Project settings</h3>
        <form id="renameForm" class="settings-form" data-project="${esc(pid)}" novalidate>
          <div class="field">
            <label for="renameInput">Project name</label>
            <input id="renameInput" maxlength="120" value="${esc(project.name)}" autocomplete="off">
          </div>
          <p class="field-error" id="renameError" role="alert"></p>
          <button class="btn btn-light btn-sm" type="submit">Save name</button>
        </form>
        <h3 class="doc-h3">Analysis history</h3>${rows}
        <div id="projectDiffArea" hidden></div>
      </div>`);
    } catch (err) {
      openDrawer(`<p class="field-error" role="alert">${esc(err.message)}</p>`);
    }
  }

  async function renameProject(form) {
    const name = form.querySelector('input').value.trim();
    const err = document.getElementById('renameError');
    err.textContent = '';
    if (!name || name.length > 120) { err.textContent = 'Enter a name between 1 and 120 characters.'; return; }
    try {
      await api(`/projects/${encodeURIComponent(form.dataset.project)}`, { method: 'PATCH', json: { name } });
      toast('Project renamed');
      loadProjects();
      openProject(form.dataset.project);
    } catch (e) {
      err.textContent = e.message;
    }
  }

  async function showScanDiff(currId, baseId) {
    const area = document.getElementById('projectDiffArea');
    if (!area) return;
    area.hidden = false;
    area.innerHTML = '<p class="muted">Comparing scans…</p>';
    try {
      const diff = await api(`/analyses/${encodeURIComponent(currId)}/diff?against=${encodeURIComponent(baseId)}`);
      const s = diff.summary;
      const netSign = s.delta > 0 ? '+' : '';
      const newItems = diff.new && diff.new.length
        ? `<div style="margin-top:10px"><strong>Newly introduced (${diff.new.length})</strong><ul style="padding-left:18px;margin:6px 0;font-size:12.5px">${diff.new.slice(0, 5).map((f) => `<li class="muted"><code style="color:var(--red)">${esc(f.ruleId)}</code> in ${esc(f.filePath)}:${f.startLine}</li>`).join('')}${diff.new.length > 5 ? `<li class="muted">… and ${diff.new.length - 5} more</li>` : ''}</ul></div>` : '';
      const resolvedItems = diff.resolved && diff.resolved.length
        ? `<div style="margin-top:10px"><strong>Resolved (${diff.resolved.length})</strong><ul style="padding-left:18px;margin:6px 0;font-size:12.5px">${diff.resolved.slice(0, 5).map((f) => `<li class="muted"><code style="color:#3FB950">${esc(f.ruleId)}</code> in ${esc(f.filePath)}:${f.startLine}</li>`).join('')}${diff.resolved.length > 5 ? `<li class="muted">… and ${diff.resolved.length - 5} more</li>` : ''}</ul></div>` : '';

      area.innerHTML = `
        <div class="panel" style="background:var(--surf);border-color:var(--border-strong);padding:16px;margin-top:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <h4 style="margin:0;font-size:14px">Scan Comparison</h4>
            <span class="mono tag" style="font-size:11px">Net ${netSign}${s.delta}</span>
          </div>
          <div style="display:flex;gap:12px;font-size:13px;flex-wrap:wrap">
            <span style="color:var(--red)">+${s.newCount} New</span>
            <span style="color:#3FB950">-${s.resolvedCount} Resolved</span>
            <span class="muted">${s.persistingCount} Persistent</span>
          </div>
          ${newItems}
          ${resolvedItems}
        </div>`;
    } catch (e) {
      area.innerHTML = `<p class="field-error">${esc(e.message)}</p>`;
    }
  }

  /* ---------- Reports ---------- */
  let reportAid = null;
  async function loadReports() {
    reportAid = await loadAnalysisPicker('reportPick', state.analysis);
    renderReport();
  }

  async function renderReport() {
    const body = document.getElementById('reportBody');
    const preview = document.getElementById('mdPreview');
    ['repJson', 'repMd', 'repSarif'].forEach((id) => {
      const b = document.getElementById(id);
      b.disabled = !reportAid;
      b.classList.toggle('disabled', !reportAid);
      b.setAttribute('aria-disabled', reportAid ? 'false' : 'true');
      b.dataset.href = reportAid ? `/api/analyses/${reportAid}/report?format=${b.dataset.format}` : '';
      b.onclick = reportAid ? () => { window.location.assign(b.dataset.href); } : null;
    });
    if (!reportAid) { body.innerHTML = '<p class="muted">Select a completed analysis.</p>'; preview.hidden = true; return; }
    try {
      const r = await api(`/analyses/${reportAid}/report?format=json`);
      const s = r.summary;
      body.innerHTML = `
        <h2 class="detail-title">${esc(r.analysis.projectName)}</h2>
        <p class="muted mono">${esc(r.analysis.projectSource)} · ${fmtDate(r.analysis.createdAt)}${r.analysis.partial ? ' · partial result' : ''}</p>
        <div class="grid-4" style="margin-top:20px">
          ${SEV.map((x) => `<div><div class="stat-label">${sevLabel(x)}</div><div class="stat-num small">${s.bySeverity[x]}</div></div>`).join('')}
        </div>
        <p style="margin-top:20px">${s.total} findings across ${r.analysis.filesScanned} files. The JSON export includes evidence, excerpts, verification steps and triage history for each finding.</p>`;
      if (!preview.hidden) loadPreview();
    } catch (err) {
      body.innerHTML = `<p class="field-error" role="alert">${esc(err.message)}</p>`;
    }
  }

  async function loadPreview() {
    const preview = document.getElementById('mdPreview');
    if (!reportAid) return;
    try {
      const res = await fetch(`/api/analyses/${reportAid}/report?format=markdown`);
      preview.textContent = await res.text();
    } catch (_) {
      preview.textContent = 'The Markdown report could not be loaded.';
    }
  }

  /* ---------- Wiring ---------- */
  function bind() {
    document.getElementById('checkBtn').addEventListener('click', checkRepository);
    document.getElementById('analyzeUrl').addEventListener('input', () => {
      document.getElementById('startBtn').disabled = document.getElementById('analyzeUrl').value.trim() === '';
      document.getElementById('branch').disabled = true;
      document.getElementById('branch').innerHTML = '<option value="">Check the repository to list branches</option>';
    });
    document.getElementById('analyzeForm').addEventListener('submit', (e) => e.preventDefault());
    document.getElementById('uploadForm').addEventListener('submit', (e) => e.preventDefault());
    document.getElementById('zipFile').addEventListener('change', (e) => {
      const f = e.target.files[0];
      document.getElementById('startBtn').disabled = !f;
      document.getElementById('analyzeUrl').value = '';
    });
    document.getElementById('startBtn').addEventListener('click', () => {
      const hasUrl = document.getElementById('analyzeUrl').value.trim();
      const hasZip = document.getElementById('zipFile').files.length > 0;
      if (hasUrl) startFromUrl();
      else if (hasZip) startFromZip();
      else setFieldError('analyzeErr', 'Enter a repository URL or choose a ZIP archive.');
    });
    document.getElementById('cancelBtn').addEventListener('click', cancelRun);
    document.getElementById('viewFindingsBtn').addEventListener('click', () => {
      state.analysis = state.run ? state.run.id : state.analysis;
      location.hash = '#findings';
    });
    document.getElementById('viewReportBtn').addEventListener('click', () => { location.hash = '#reports'; });

    document.getElementById('analysisPick').addEventListener('change', (e) => {
      state.analysis = e.target.value; localStorage.setItem('nexus.analysis', state.analysis); renderFindings();
    });
    document.getElementById('findQ').addEventListener('input', () => {
      clearTimeout(state.findingsTimer); state.findingsTimer = setTimeout(renderFindings, 180);
    });
    document.getElementById('findStatus').addEventListener('change', renderFindings);
    document.querySelectorAll('[data-sev-filter]').forEach((b) => b.addEventListener('click', () => {
      state.filter.sev = b.dataset.sevFilter;
      document.querySelectorAll('[data-sev-filter]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      renderFindings();
    }));
    document.querySelectorAll('[data-cat-filter]').forEach((b) => b.addEventListener('click', () => {
      state.filter.cat = b.dataset.catFilter;
      document.querySelectorAll('[data-cat-filter]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      renderFindings();
    }));
    document.getElementById('findList').addEventListener('click', (e) => {
      const b = e.target.closest('[data-fid]'); if (b && state.analysis) openFinding(state.analysis, b.dataset.fid);
    });

    document.getElementById('projQ').addEventListener('input', loadProjects);
    document.getElementById('projList').addEventListener('click', (e) => {
      const b = e.target.closest('[data-project]'); if (b) openProject(b.dataset.project);
    });

    document.getElementById('reportPick').addEventListener('change', (e) => {
      reportAid = e.target.value; state.analysis = reportAid; localStorage.setItem('nexus.analysis', reportAid); renderReport();
    });
    document.getElementById('togglePreview').addEventListener('click', async (e) => {
      const preview = document.getElementById('mdPreview');
      const open = preview.hidden;
      preview.hidden = !open;
      e.currentTarget.setAttribute('aria-expanded', String(open));
      e.currentTarget.textContent = open ? 'Hide Markdown' : 'Show Markdown';
      if (open) await loadPreview();
    });
    document.getElementById('copyMd').addEventListener('click', async () => {
      if (!reportAid) return toast('Select a completed analysis first.');
      const res = await fetch(`/api/analyses/${reportAid}/report?format=markdown`);
      const text = await res.text();
      try { await navigator.clipboard.writeText(text); toast('Markdown copied'); }
      catch (_) { toast('Clipboard is unavailable here. Use Download Markdown instead.'); }
    });

    const delegated = (e) => {
      const t = e.target;
      const finding = t.closest('[data-open-finding]');
      if (finding) return openFinding(state.analysis, finding.dataset.openFinding);
      const an = t.closest('[data-open-analysis]');
      if (an) return openAnalysis(an.dataset.openAnalysis);
      const fa = t.closest('[data-findings-for]');
      if (fa) { state.analysis = fa.dataset.findingsFor; localStorage.setItem('nexus.analysis', state.analysis); closeDrawer(); location.hash = '#findings'; return; }
      const ra = t.closest('[data-report-for]');
      if (ra) { state.analysis = ra.dataset.reportFor; localStorage.setItem('nexus.analysis', state.analysis); closeDrawer(); location.hash = '#reports'; return; }
      if (t.closest('#aiBtn')) { const w = t.closest('.detail'); if (w) explainWithAI(w); return; }
      const cat = t.closest('[data-go-findings]');
      if (cat) { state.filter.cat = cat.dataset.goFindings; location.hash = '#findings'; return; }
      const diffBtn = t.closest('[data-diff-curr]');
      if (diffBtn) { showScanDiff(diffBtn.dataset.diffCurr, diffBtn.dataset.diffBase); return; }
    };
    document.getElementById('dash').addEventListener('click', delegated);
    document.getElementById('drawer').addEventListener('click', delegated);
    document.getElementById('ringVal').style.strokeDasharray = RING;
    document.getElementById('drawerClose').addEventListener('click', closeDrawer);
    document.getElementById('dim').addEventListener('click', closeDrawer);
    document.getElementById('drawer').addEventListener('submit', (e) => {
      if (e.target.id === 'triageForm') { e.preventDefault(); applyStatus(e.target); }
      if (e.target.id === 'renameForm') { e.preventDefault(); renameProject(e.target); }
    });
    document.getElementById('dash').addEventListener('submit', (e) => e.preventDefault());
    window.addEventListener('resize', moveIndicator);
  }

  /* ---------- Count tween: numbers move from their last shown value to the persisted one ---------- */
  function tweenNumber(id, to) {
    const el = document.getElementById(id);
    if (!el) return;
    cancelAnimationFrame(el._raf);
    if (typeof to !== 'number' || Number.isNaN(to)) { el.textContent = String(to); delete el.dataset.value; return; }
    const from = Number(el.dataset.value);
    el.dataset.value = String(to);
    if (!Number.isFinite(from) || from === to || reducedMotion()) { el.textContent = String(to); return; }
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / 480);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(from + (to - from) * eased));
      if (p < 1) el._raf = requestAnimationFrame(step);
    };
    el._raf = requestAnimationFrame(step);
  }

  /* ---------- Analysis island: states come only from server events and persisted reads ---------- */
  const island = document.getElementById('island');
  const islandAction = document.getElementById('islandAction');
  let runFindings = 0;
  let contractTimer = null;

  function setIsland(stateName, title, sub) {
    island.dataset.state = stateName;
    setText('islandTitle', title);
    const s = document.getElementById('islandSub');
    s.textContent = sub;
    s.classList.remove('swap');
    void s.offsetWidth;
    s.classList.add('swap');
  }
  function setIslandCount(n) {
    const c = document.getElementById('islandCount');
    c.hidden = n == null;
    if (n != null) tweenNumber('islandCount', n);
  }
  function setIslandAction(label, handler) {
    islandAction.hidden = !label;
    islandAction.textContent = label || '';
    islandAction.onclick = handler || null;
  }
  const IDLE_TEXT = {
    live: ['Live updates', 'Watching the server for changes'],
    connecting: ['Live updates', 'Connecting to the event stream'],
    reconnecting: ['Reconnecting', 'Stream interrupted; resyncing from the server'],
    polling: ['Live updates', 'Stream unavailable; checking every few seconds'],
    idle: ['Live updates', 'Sign in to receive updates'],
  };
  function showIdle() {
    clearTimeout(contractTimer);
    const [t, s] = IDLE_TEXT[window.NexusLive ? NexusLive.status : 'idle'] || IDLE_TEXT.idle;
    setIsland('idle', t, s);
    setIslandCount(null);
    setIslandAction(null);
  }

  let refreshTimer = null;
  function refreshView() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      if (state.view === 'overview') loadOverview();
      else if (state.view === 'findings') loadFindingsPage();
      else if (state.view === 'projects') loadProjects();
      else if (state.view === 'reports') loadReports();
    }, 180);
  }

  function handleAnalysisEvent(ev) {
    const stageLabel = (key) => { const hit = STAGES.find(([k]) => k === key); return hit ? hit[1] : 'Working'; };
    clearTimeout(contractTimer);
    switch (ev.type) {
      case 'analysis.queued':
        runFindings = 0;
        setIsland('queued', 'Queued', ev.data.projectName || 'Waiting for a worker');
        setIslandCount(null);
        setIslandAction(null);
        break;
      case 'analysis.started':
        runFindings = 0;
        setIsland('analyzing', 'Analyzing', 'Starting');
        setIslandCount(null);
        setIslandAction(null);
        break;
      case 'analysis.stage_updated':
        setIsland('analyzing', 'Analyzing', stageLabel(ev.data.stage));
        break;
      case 'analysis.completed':
        // The findings view follows the newest completed analysis, so its refresh reads the run that just finished.
        state.analysis = ev.analysisId;
        localStorage.setItem('nexus.analysis', ev.analysisId);
        setIsland('completed', 'Analysis complete',
          `${ev.data.partial ? 'Partial result · ' : ''}findings stored and ready to review`);
        setIslandCount(ev.data.findingCount);
        setIslandAction('Open', () => openAnalysis(ev.analysisId));
        contractTimer = setTimeout(showIdle, 8000);
        break;
      case 'analysis.failed':
        setIsland('failed', ev.data.status === 'cancelled' ? 'Analysis cancelled' : 'Analysis failed',
          ev.data.errorMessage || 'The analysis did not finish');
        setIslandCount(null);
        setIslandAction('Open', () => openAnalysis(ev.analysisId));
        break;
      default: break;
    }
  }

  function handleFindingEvent(ev) {
    if (ev.type !== 'finding.created') return;
    runFindings += 1;
    setIsland('findings', 'Findings discovered', `${ev.data.title} · ${cap(ev.data.severity || 'open')}`);
    setIslandCount(runFindings);
  }

  if (window.NexusLive) {
    NexusLive.subscribe((ev) => {
      if (ev.type === 'resync') {
        refreshView();
        if (state.run) state.run.tick();
        return;
      }
      if (ev.type.startsWith('analysis.')) handleAnalysisEvent(ev);
      else if (ev.type === 'finding.created') handleFindingEvent(ev);
      if (state.run && ev.analysisId === state.run.id) state.run.tick();
      refreshView();
    });
    NexusLive.onStatus(() => { if (island.dataset.state === 'idle') showIdle(); });
    if (window.NexusAuth) {
      NexusAuth.ready.then(() => { if (NexusAuth.user) NexusLive.connect(); });
    }
    window.addEventListener('nexus:unauthorized', () => NexusLive.stop());
  }

  bind();
  checkHealth();
  setInterval(checkHealth, 15000);
  // Resume watching a run that is still queued or running when the page loads.
  if (state.analysis) {
    api(`/analyses/${state.analysis}`).then((a) => {
      if (!TERMINAL.has(a.status)) watchAnalysis(a.id);
    }).catch(() => {});
  }
})();
