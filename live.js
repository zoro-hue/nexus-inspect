/* Live connection: one EventSource per tab to /api/events, with resync after reconnecting and a polling fallback.
   Events are notifications; every view re-reads persisted state from the API, so the UI never trusts the stream alone. */
(() => {
  const TYPES = [
    'project.created', 'analysis.queued', 'analysis.started', 'analysis.stage_updated',
    'analysis.completed', 'analysis.failed', 'finding.created', 'finding.updated',
    'finding.resolved', 'report.generated',
  ];
  const FAILURES_BEFORE_POLLING = 3;
  const POLL_MS = 4000;
  const STALE_MS = 40000;      // the server heartbeats every 15 s; silence for longer means the connection is dead
  const WATCHDOG_MS = 10000;

  const handlers = new Set();
  const statusListeners = new Set();
  const state = { es: null, status: 'idle', failures: 0, everOpen: false, poll: null, lastSeen: 0, retry: null };

  function setStatus(next) {
    if (state.status === next) return;
    state.status = next;
    statusListeners.forEach((fn) => fn(next));
  }

  function emit(event) {
    handlers.forEach((fn) => { try { fn(event); } catch (err) { console.error(err); } });
  }

  function startPolling() {
    if (state.poll) return;
    setStatus('polling');
    state.poll = setInterval(() => emit({ type: 'resync', source: 'poll' }), POLL_MS);
  }

  function stopPolling() {
    clearInterval(state.poll);
    state.poll = null;
  }

  /* A closed EventSource means the server refused the stream (usually an expired session). Confirm before signing out. */
  async function checkSession() {
    try {
      const res = await fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' });
      const body = await res.json();
      if (!body.user) { window.dispatchEvent(new Event('nexus:unauthorized')); return; }
      // The session is valid, so the stream was refused for another reason (for example, too many open connections).
      // EventSource does not retry a refused stream on its own, so schedule the retry here.
      setStatus('reconnecting');
      clearTimeout(state.retry);
      state.retry = setTimeout(connect, 15000);
    } catch (_) {
      startPolling();
    }
  }

  function dropStream() {
    if (state.es) state.es.close();
    state.es = null;
  }

  function connect() {
    if (state.es || !window.NexusAuth || !window.NexusAuth.user) return;
    clearTimeout(state.retry);
    if (typeof EventSource === 'undefined') { startPolling(); return; }
    if (navigator.onLine === false) { setStatus('reconnecting'); return; }
    setStatus(state.everOpen ? 'reconnecting' : 'connecting');
    const es = new EventSource('/api/events');
    state.es = es;
    state.lastSeen = Date.now();

    es.onopen = () => {
      const wasDown = state.everOpen && state.status !== 'live';
      state.everOpen = true;
      state.failures = 0;
      state.lastSeen = Date.now();
      stopPolling();
      setStatus('live');
      // Anything that happened while disconnected is read from the API, not replayed into the UI.
      if (wasDown) emit({ type: 'resync', source: 'reconnect' });
    };

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        state.es = null;
        checkSession();
        return;
      }
      state.failures += 1;
      setStatus(state.everOpen ? 'reconnecting' : 'connecting');
      if (state.failures >= FAILURES_BEFORE_POLLING) startPolling();
    };

    const touch = () => { state.lastSeen = Date.now(); };
    es.addEventListener('heartbeat', touch);
    TYPES.forEach((type) => {
      es.addEventListener(type, (message) => {
        touch();
        let payload;
        try { payload = JSON.parse(message.data); } catch (_) { return; }
        emit({ type, id: payload.id, projectId: payload.projectId, analysisId: payload.analysisId,
               data: payload.data || {}, createdAt: payload.createdAt });
      });
    });
  }

  /* Reconnect after the stream goes silent (for example, a sleeping laptop or a proxy that stopped forwarding). */
  setInterval(() => {
    if (state.es && Date.now() - state.lastSeen > STALE_MS) {
      dropStream();
      setStatus('reconnecting');
      connect();
    }
  }, WATCHDOG_MS);

  window.addEventListener('offline', () => { dropStream(); setStatus('reconnecting'); });
  window.addEventListener('online', () => connect());

  function stop() {
    dropStream();
    stopPolling();
    setStatus('idle');
  }

  window.NexusLive = {
    connect,
    stop,
    subscribe(fn) { handlers.add(fn); return () => handlers.delete(fn); },
    onStatus(fn) { statusListeners.add(fn); fn(state.status); return () => statusListeners.delete(fn); },
    get status() { return state.status; },
    get isLive() { return state.status === 'live'; },
  };
})();
