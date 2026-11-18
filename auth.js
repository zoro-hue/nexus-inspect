/* Authentication UI: session state, sign-in and sign-up forms, profile menu and sign-out.
   Talks only to /api/auth/*. Never simulates a successful sign-in: the session cookie set by the server is the only proof. */
(() => {
  const API = '/api/auth';
  const NEXT_KEY = 'nexus.next';
  const MSG_KEY = 'nexus.authMessage';
  const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const state = { user: null, loaded: false };

  /* ---------- Requests ---------- */
  async function request(path, body) {
    const opts = { method: body === undefined ? 'GET' : 'POST', headers: {}, cache: 'no-store', credentials: 'same-origin' };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(API + path, opts);
    } catch (_) {
      throw Object.assign(new Error('The NEXUS server cannot be reached. Check that it is running.'), { code: 'offline', fields: {} });
    }
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    return { ok: res.ok, status: res.status, data };
  }

  function errorOf(result, fallback) {
    const err = (result.data && result.data.error) || {};
    return Object.assign(new Error(err.message || fallback), { code: err.code || 'http_' + result.status, fields: err.fields || {} });
  }

  /* Loads the session once at startup. A missing or expired session simply means signed out. */
  async function refresh() {
    try {
      const r = await request('/me');
      state.user = r.ok ? r.data.user : null;
    } catch (_) {
      state.user = null;
    }
    state.loaded = true;
    syncAllAuth(state.user);
    return state.user;
  }

  /* ---------- Validation (mirrors the server rules, which remain authoritative) ---------- */
  function validateSignIn(email, password) {
    const f = {};
    if (!email.trim()) f.email = 'Enter your email address.';
    if (!password) f.password = 'Enter your password.';
    return f;
  }

  function validateSignUp(name, email, password) {
    const f = {};
    if (!name.trim() || name.trim().length > 80) f.name = 'Enter a name between 1 and 80 characters.';
    if (!EMAIL_RE.test(email.trim())) f.email = 'Enter a valid email address.';
    if (password.length < 10) f.password = 'Use at least 10 characters.';
    else if (password.length > 128) f.password = 'Use at most 128 characters.';
    else if (!(/[A-Za-z]/.test(password) && /\d/.test(password))) f.password = 'Use at least one letter and one number.';
    return f;
  }

  /* ---------- Form helpers ---------- */
  function showFieldErrors(form, fields) {
    form.querySelectorAll('.field').forEach((field) => {
      const name = field.dataset.field;
      const msg = field.querySelector('.field-error');
      const text = fields[name] || '';
      field.classList.toggle('error', Boolean(text));
      if (msg) msg.textContent = text;
      const input = field.querySelector('input');
      if (input) input.setAttribute('aria-invalid', text ? 'true' : 'false');
    });
  }

  function setAlert(message) {
    const box = $('#authAlert');
    if (!message) { box.hidden = true; box.textContent = ''; return; }
    box.textContent = message;
    box.hidden = false;
  }

  function setBusy(button, busy, idleLabel, busyLabel) {
    button.disabled = busy;
    button.setAttribute('aria-busy', String(busy));
    button.textContent = busy ? busyLabel : idleLabel;
  }

  function bindPasswordToggle(btn) {
    const input = document.getElementById(btn.getAttribute('aria-controls'));
    btn.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-pressed', String(show));
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.textContent = show ? 'Hide' : 'Show';
    });
  }

  /* ---------- Auth page ---------- */
  let authMode = 'signin';

  function showAuthPage(mode) {
    authMode = mode;
    const signin = $('#signinForm');
    const signup = $('#signupForm');
    signin.hidden = mode !== 'signin';
    signup.hidden = mode !== 'signup';
    $('#authTitle').textContent = mode === 'signin' ? 'Welcome back' : 'Create your account';
    $('#authKicker').textContent = mode === 'signin' ? 'Sign in' : 'Sign up';
    document.title = mode === 'signin' ? 'Sign in — NEXUS INSPECT' : 'Sign up — NEXUS INSPECT';
    $('#authSwitch').innerHTML = mode === 'signin'
      ? 'New to NEXUS? <a href="#signup">Create an account</a>'
      : 'Already have an account? <a href="#signin">Sign in</a>';
    setAlert(sessionStorage.getItem(MSG_KEY));
    sessionStorage.removeItem(MSG_KEY);
    const form = mode === 'signin' ? signin : signup;
    showFieldErrors(form, {});
    setTimeout(() => form.querySelector('input')?.focus(), 60);
  }

  function nextTarget() {
    const next = sessionStorage.getItem(NEXT_KEY);
    sessionStorage.removeItem(NEXT_KEY);
    return next && next !== 'signin' && next !== 'signup' ? next : 'overview';
  }

  /* A successful sign-in or sign-out reloads the app so no data from a previous session stays on screen. */
  function reloadAt(hash) {
    // replaceState changes the URL without a hashchange event, so the message stored for the next page survives.
    history.replaceState(null, '', '/#' + hash);
    location.reload();
  }

  function bindForms() {
    const signin = $('#signinForm');
    const signup = $('#signupForm');

    signin.addEventListener('submit', async (e) => {
      e.preventDefault();
      setAlert('');
      const email = signin.email.value;
      const password = signin.password.value;
      const fields = validateSignIn(email, password);
      showFieldErrors(signin, fields);
      if (Object.keys(fields).length) return;
      const btn = signin.querySelector('button[type=submit]');
      setBusy(btn, true, 'Sign in', 'Signing in…');
      try {
        const r = await request('/signin', { email: email.trim(), password });
        if (!r.ok) throw errorOf(r, 'Sign-in failed.');
        state.user = r.data.user;
        reloadAt(nextTarget());
      } catch (err) {
        showFieldErrors(signin, err.fields || {});
        setAlert(err.message);
        setBusy(btn, false, 'Sign in', 'Signing in…');
      }
    });

    signup.addEventListener('submit', async (e) => {
      e.preventDefault();
      setAlert('');
      const name = signup.name.value;
      const email = signup.email.value;
      const password = signup.password.value;
      const fields = validateSignUp(name, email, password);
      showFieldErrors(signup, fields);
      if (Object.keys(fields).length) return;
      const btn = signup.querySelector('button[type=submit]');
      setBusy(btn, true, 'Create account', 'Creating account…');
      try {
        const r = await request('/signup', { name: name.trim(), email: email.trim(), password });
        if (!r.ok) throw errorOf(r, 'Account creation failed.');
        state.user = r.data.user;
        reloadAt(nextTarget());
      } catch (err) {
        showFieldErrors(signup, err.fields || {});
        setAlert(err.message);
        setBusy(btn, false, 'Create account', 'Creating account…');
      }
    });

    document.querySelectorAll('.pw-toggle').forEach(bindPasswordToggle);
  }

  /* ---------- Profile menu ---------- */
  function initials(user) {
    const parts = (user.name || user.email || '?').trim().split(/\s+/);
    return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }

  function renderProfile() {
    const user = state.user;
    const btn = $('#profileBtn');
    if (!btn) return;
    btn.hidden = !user;
    if (!user) { closeProfile(); return; }
    $('#profileInitials').textContent = initials(user);
    $('#profileName').textContent = user.name;
    $('#profileEmail').textContent = user.email;
    $('#profileSince').textContent = user.createdAt ? new Date(user.createdAt).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '—';
  }

  /* Centralized UI synchronization across ALL headers, pages, and landing page CTAs */
  function syncAllAuth(user) {
    // 1. Dashboard profile
    renderProfile();

    // 2. Marketing header (gnav)
    const gnavGuest = $('#gnavActions .auth-guest-only');
    const gnavUser = $('#gnavUserWrap');
    if (gnavGuest && gnavUser) {
      gnavGuest.hidden = !!user;
      gnavUser.hidden = !user;
    }
    if (user) {
      const gInit = $('#gnavProfileInitials');
      const gName = $('#gnavProfileName');
      const gEmail = $('#gnavProfileEmail');
      if (gInit) gInit.textContent = initials(user);
      if (gName) gName.textContent = user.name;
      if (gEmail) gEmail.textContent = user.email;
    }

    // 3. Documentation header
    const docsGuest = $('#docsTopActions .auth-guest-only');
    const docsUser = $('#docsTopActions .auth-user-only');
    if (docsGuest && docsUser) {
      docsGuest.hidden = !!user;
      docsUser.hidden = !user;
    }

    // 4. Home landing page CTAs
    if (window.updateHomeAuth) {
      window.updateHomeAuth(user);
    }

    // 5. Global dispatch
    window.dispatchEvent(new CustomEvent('nexus:auth-changed', { detail: { user } }));
  }

  function bindGnavProfile() {
    const btn = $('#gnavProfileBtn');
    const menu = $('#gnavProfileMenu');
    if (btn && menu) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = !menu.hidden && menu.classList.contains('open');
        if (isOpen) {
          menu.classList.remove('open');
          btn.setAttribute('aria-expanded', 'false');
          setTimeout(() => { if (!menu.classList.contains('open')) menu.hidden = true; }, 180);
        } else {
          menu.hidden = false;
          btn.setAttribute('aria-expanded', 'true');
          requestAnimationFrame(() => menu.classList.add('open'));
        }
      });
      document.addEventListener('click', (e) => {
        if (!menu.hidden && !e.target.closest('#gnavProfile')) {
          menu.classList.remove('open');
          btn.setAttribute('aria-expanded', 'false');
          setTimeout(() => { if (!menu.classList.contains('open')) menu.hidden = true; }, 180);
        }
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !menu.hidden) {
          menu.classList.remove('open');
          btn.setAttribute('aria-expanded', 'false');
          setTimeout(() => { if (!menu.classList.contains('open')) menu.hidden = true; }, 180);
          btn.focus();
        }
      });
    }
    const gSignOut = $('#gnavSignOutBtn');
    if (gSignOut) gSignOut.addEventListener('click', signOut);
    const dSignOut = $('#docsSignOutBtn');
    if (dSignOut) dSignOut.addEventListener('click', signOut);
  }

  async function checkCrossTabSync() {
    try {
      const r = await request('/me');
      const latestUser = r.ok ? r.data.user : null;
      const changed = (latestUser && !state.user) || (!latestUser && state.user) || (latestUser && state.user && latestUser.id !== state.user.id);
      if (changed) {
        state.user = latestUser;
        syncAllAuth(latestUser);
        if (!latestUser && location.hash.startsWith('#overview')) {
          location.hash = 'signin';
        }
      }
    } catch (_) {}
  }
  window.addEventListener('focus', checkCrossTabSync);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkCrossTabSync(); });

  function openProfile() {
    const menu = $('#profileMenu');
    menu.hidden = false;
    $('#profileBtn').setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => menu.classList.add('open'));
  }

  function closeProfile() {
    const menu = $('#profileMenu');
    if (!menu) return;
    menu.classList.remove('open');
    $('#profileBtn').setAttribute('aria-expanded', 'false');
    setTimeout(() => { if (!menu.classList.contains('open')) menu.hidden = true; }, 180);
  }

  function bindProfile() {
    const btn = $('#profileBtn');
    const menu = $('#profileMenu');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (menu.hidden) openProfile(); else closeProfile();
    });
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !e.target.closest('#profile')) closeProfile();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !menu.hidden) { closeProfile(); btn.focus(); }
    });
    $('#profileDetailsBtn').addEventListener('click', () => {
      const details = $('#profileDetails');
      details.hidden = !details.hidden;
      $('#profileDetailsBtn').setAttribute('aria-expanded', String(!details.hidden));
    });
    $('#signOutBtn').addEventListener('click', signOut);
  }

  async function signOut() {
    const btn = $('#signOutBtn');
    btn.disabled = true;
    try {
      await request('/signout', {});
    } catch (_) {
      /* The server may be unreachable; the local session is still cleared below. */
    }
    state.user = null;
    localStorage.removeItem('nexus.analysis');
    sessionStorage.setItem(MSG_KEY, 'You have signed out.');
    reloadAt('signin');
  }

  /* ---------- Protection ---------- */
  /* Called by dash.js when any dashboard request returns 401 (expired or revoked session). */
  function sessionEnded() {
    if (!state.user) return;
    state.user = null;
    renderProfile();
    sessionStorage.setItem(NEXT_KEY, location.hash.slice(1) || 'overview');
    sessionStorage.setItem(MSG_KEY, 'Your session has ended. Sign in again to continue.');
    location.hash = 'signin';
  }

  window.addEventListener('nexus:unauthorized', sessionEnded);

  /* Called by app.js before a dashboard route renders. Returns true only for a signed-in user. */
  function requireUser(route) {
    if (state.user) return true;
    sessionStorage.setItem(NEXT_KEY, route);
    sessionStorage.setItem(MSG_KEY, 'Sign in to open the dashboard.');
    location.hash = 'signin';
    return false;
  }

  window.NexusAuth = {
    get user() { return state.user; },
    get loaded() { return state.loaded; },
    ready: null,
    showAuthPage,
    requireUser,
    signOut,
  };

  bindForms();
  bindProfile();
  bindGnavProfile();
  window.NexusAuth.ready = refresh();
})();
