/* Shared helpers, routing, marketing header, mega menu, mobile sheet, page transitions. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, reduced ? Math.min(ms, 120) : ms));
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('on'), 2600);
}

/* ---------- Routing (plain #names only) ---------- */
const DASH_VIEWS = ['overview', 'analyze', 'findings', 'projects', 'reports'];
let lastMode = null;

function enterPage(el) {
  if (reduced) return;
  el.classList.remove('page-enter');
  void el.offsetWidth; // restart the animation
  el.classList.add('page-enter');
}

const AUTH_VIEWS = ['signin', 'signup'];

function route() {
  if (!window.NexusAuth || !window.NexusAuth.loaded) return; // wait for the session check
  const [h, slug = ''] = (location.hash.slice(1) || 'home').split('/');
  const isDash = DASH_VIEWS.includes(h);
  const isAuth = AUTH_VIEWS.includes(h);
  const isDocs = h === 'docs' || h === 'api';
  closeMega();
  closeMobileSheet();
  closeAllPops();
  closeDrawer();
  if (isAuth && window.NexusAuth.user) { location.hash = 'overview'; return; }
  if (isDash && !window.NexusAuth.requireUser(h)) return;
  const mode = isDash ? 'dash' : isAuth ? 'auth' : isDocs ? 'docs' : 'marketing';
  const marketing = $('#marketing');
  const dash = $('#dash');
  const authPage = $('#auth');
  const docsPage = $('#docsPage');
  marketing.hidden = mode !== 'marketing';
  dash.hidden = mode !== 'dash';
  authPage.hidden = mode !== 'auth';
  docsPage.hidden = mode !== 'docs';
  document.body.dataset.mode = mode;
  const shown = mode === 'dash' ? dash : mode === 'auth' ? authPage : mode === 'docs' ? docsPage : marketing;
  if (mode !== lastMode) enterPage(shown);
  if (mode !== 'marketing' && window.NexusHome && window.NexusHome.cleanup) {
    window.NexusHome.cleanup();
  } else if (mode === 'marketing' && lastMode && lastMode !== 'marketing' && window.NexusHome && window.NexusHome.init) {
    window.NexusHome.init();
  }
  lastMode = mode;
  if (isAuth) {
    window.NexusAuth.showAuthPage(h);
  } else if (isDocs) {
    window.scrollTo({ top: 0, behavior: 'auto' });
    window.NexusDocs.show(h, slug);
  } else if (isDash) {
    window.showView(h);
  } else {
    const el = h === 'home' ? null : document.getElementById(h);
    if (el) el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
    else window.scrollTo({ top: 0, behavior: reduced ? 'auto' : 'smooth' });
    document.title = 'NEXUS INSPECT — Your code deserves a second pair of eyes.';
  }
}
window.addEventListener('hashchange', route);

/* ---------- Sticky header ---------- */
const gnav = $('#gnav');
function onScrollMarketing() { gnav.classList.toggle('scrolled', window.scrollY > 24); }
let ticking = false;
window.addEventListener('scroll', () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => { onScrollMarketing(); ticking = false; });
}, { passive: true });

/* ---------- Mega menu ---------- */
const mega = $('#mega');
const dim = $('#dim');
const gnavPill = $('#gnavPill');
const menuTriggers = $$('[data-menu]');
let openName = null;
let closeTimer = null;
let hoverTimer = null;
const isReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function moveGnavPill(btn) {
  if (!gnavPill) return;
  if (!btn) {
    gnavPill.classList.remove('on');
    return;
  }
  gnavPill.style.width = btn.offsetWidth + 'px';
  gnavPill.style.transform = `translateX(${btn.offsetLeft}px)`;
  gnavPill.classList.add('on');
}

/* Link entrance for the shared panel: each link slides in from the travel direction and sharpens, staggered. */
function staggerIn(panel, dir) {
  panel.querySelectorAll('.mega-link').forEach((el, i) => {
    el.animate([
      { opacity: 0, transform: `translate(${dir * 24}px, 8px)`, filter: 'blur(5px)' },
      { opacity: 1, transform: 'translate(0, 0)', filter: 'blur(0px)' }
    ], { duration: 420, delay: i * 45, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' });
  });
}

// Shared dropdown: one panel is visible at a time. A switch hands off
// sequentially (outgoing exits alone, then incoming reveals), so labels never
// overlap. Interrupted switches are finalised immediately so no panel stays
// half-hidden.
let shownPanel = null;
let exitingPanel = null;
let switchTimer = null;

function resetPanelStyles(p) {
  p.style.position = '';
  p.style.top = '';
  p.style.left = '';
  p.style.right = '';
  p.style.pointerEvents = '';
  p.style.transform = '';
  p.style.opacity = '';
  p.style.visibility = '';
}

function finalizeExit() {
  clearTimeout(switchTimer);
  switchTimer = null;
  if (exitingPanel) {
    const grid = exitingPanel.querySelector('.mega-grid') || exitingPanel;
    grid.getAnimations().forEach((a) => a.cancel());
    exitingPanel.hidden = true;
    resetPanelStyles(exitingPanel);
    exitingPanel = null;
  }
}

function setMegaHeight(panel) {
  const inner = $('.mega-inner');
  const targetH = Math.min((inner ? inner.scrollHeight : panel.scrollHeight + 76), window.innerHeight - gnav.offsetHeight - 8);
  mega.style.height = targetH + 'px';
}

function revealPanel(panel, dir, reduced) {
  resetPanelStyles(panel);
  panel.hidden = false;
  if (!reduced) staggerIn(panel, dir);
  shownPanel = panel;
  mega.classList.remove('switching');
  setMegaHeight(panel);
}

function openMega(name) {
  clearTimeout(closeTimer);
  clearTimeout(hoverTimer);
  const panel = $(`[data-panel="${name}"]`);
  if (!panel) return;
  const reduced = isReducedMotion();
  const prevName = openName;
  const wasOpen = prevName !== null && prevName !== name;
  openName = name;

  menuTriggers.forEach((t) => {
    const on = t.dataset.menu === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-expanded', String(on));
    if (on) moveGnavPill(t);
  });

  // Settle any switch still in flight, then hide every panel except the one showing.
  finalizeExit();
  $$('[data-panel]').forEach((p) => {
    if (p !== shownPanel && p !== panel) {
      p.hidden = true;
      resetPanelStyles(p);
    }
  });

  if (wasOpen && !reduced && shownPanel && shownPanel !== panel) {
    const oldPanel = shownPanel;
    const triggerArr = [...menuTriggers];
    const oldIdx = triggerArr.findIndex((t) => t.dataset.menu === prevName);
    const newIdx = triggerArr.findIndex((t) => t.dataset.menu === name);
    const dir = newIdx >= oldIdx ? 1 : -1;

    // Outgoing: exit alone, absolutely placed so it does not push the incoming panel.
    oldPanel.style.position = 'absolute';
    oldPanel.style.top = '28px';
    oldPanel.style.left = '28px';
    oldPanel.style.right = '28px';
    oldPanel.style.pointerEvents = 'none';
    const oldGrid = oldPanel.querySelector('.mega-grid') || oldPanel;
    oldGrid.animate([
      { opacity: 1, transform: 'translateX(0)', filter: 'blur(0px)' },
      { opacity: 0, transform: `translateX(${dir * -20}px)`, filter: 'blur(5px)' }
    ], { duration: 70, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards' });
    exitingPanel = oldPanel;
    shownPanel = null;
    mega.classList.add('switching');

    // Incoming: held invisible until the outgoing exit completes, then revealed.
    panel.hidden = false;
    panel.style.visibility = 'hidden';
    switchTimer = setTimeout(() => {
      switchTimer = null;
      if (openName !== name) return;
      finalizeExit();
      revealPanel(panel, dir, false);
    }, 70);
  } else {
    revealPanel(panel, 0, reduced);
  }

  mega.classList.add('open');
  dim.classList.add('on');
}

function closeMega() {
  clearTimeout(closeTimer);
  clearTimeout(hoverTimer);
  if (!openName) return;
  finalizeExit();
  shownPanel = null;
  mega.classList.remove('open', 'switching');
  mega.style.height = '0px';
  dim.classList.remove('on');
  menuTriggers.forEach((t) => { t.classList.remove('active'); t.setAttribute('aria-expanded', 'false'); });
  moveGnavPill(null);
  openName = null;
  setTimeout(() => {
    if (!openName) {
      $$('[data-panel]').forEach((p) => {
        p.hidden = true;
        p.style.position = '';
        p.style.top = '';
        p.style.left = '';
        p.style.right = '';
        p.style.pointerEvents = '';
        p.style.transform = '';
        p.style.opacity = '';
      });
    }
  }, 240);
}

menuTriggers.forEach((t, i) => {
  t.setAttribute('aria-expanded', 'false');
  t.addEventListener('pointerenter', (e) => {
    if (e.pointerType === 'mouse') {
      clearTimeout(closeTimer);
      if (openName !== null) {
        openMega(t.dataset.menu);
      } else {
        clearTimeout(hoverTimer);
        hoverTimer = setTimeout(() => openMega(t.dataset.menu), 110);
      }
    }
  });
  t.addEventListener('pointerleave', (e) => {
    if (e.pointerType === 'mouse') {
      clearTimeout(hoverTimer);
    }
  });
  t.addEventListener('click', () => {
    clearTimeout(hoverTimer);
    if (openName === t.dataset.menu) closeMega();
    else openMega(t.dataset.menu);
  });
  t.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const next = menuTriggers[(i + (e.key === 'ArrowRight' ? 1 : -1) + menuTriggers.length) % menuTriggers.length];
      next.focus();
      openMega(next.dataset.menu);
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      openMega(t.dataset.menu);
      const first = $(`[data-panel="${t.dataset.menu}"] a, [data-panel="${t.dataset.menu}"] button`);
      if (first) first.focus();
    }
  });
});

// Leaving the header region (bar plus open menu) closes after a short delay, so crossing a gap does not flicker.
gnav.addEventListener('pointerleave', (e) => {
  if (e.pointerType !== 'mouse') return;
  clearTimeout(hoverTimer);
  closeTimer = setTimeout(closeMega, 200);
});
gnav.addEventListener('pointerenter', () => {
  clearTimeout(closeTimer);
});
dim.addEventListener('click', () => { closeMega(); closeAllPops(); });
window.addEventListener('resize', () => { if (openName) openMega(openName); });

// Escape inside a panel returns focus to its trigger.
$$('[data-panel]').forEach((panel) => {
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); const name = openName; closeMega(); $(`[data-menu="${name}"]`)?.focus(); }
  });
});

/* ---------- Mobile sheet ---------- */
const sheet = $('#mobileSheet');
const menuToggle = $('#menuToggle');
function closeMobileSheet() { sheet.classList.remove('open'); menuToggle.setAttribute('aria-expanded', 'false'); }
menuToggle.addEventListener('click', () => {
  const open = !sheet.classList.contains('open');
  sheet.classList.toggle('open', open);
  menuToggle.setAttribute('aria-expanded', String(open));
  // Items stagger in on open. fill:'backwards' only, so nothing stays hidden if interrupted.
  if (open && !isReducedMotion()) {
    sheet.querySelectorAll('button, a').forEach((el, i) => {
      el.animate([
        { opacity: 0, transform: 'translateY(-8px)' },
        { opacity: 1, transform: 'translateY(0)' }
      ], { duration: 320, delay: 70 + i * 45, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' });
    });
  }
});

/* ---------- Generic actions (data-toast, data-go) ---------- */
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-toast],[data-go]');
  if (!t) return;
  if (t.dataset.toast) { e.preventDefault(); toast(t.dataset.toast); }
  if (t.dataset.go) { e.preventDefault(); location.hash = t.dataset.go; }
});

/* ---------- Escape closes everything ---------- */
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (openName) { const n = openName; closeMega(); $(`[data-menu="${n}"]`)?.focus(); }
  closeMobileSheet();
  closeAllPops();
  closeDrawer();
});

/* ---------- Init ---------- */
onScrollMarketing();
// Route the initial URL after every script has loaded, so deep links such as #findings open the right view.
// The session check runs first (auth.js), so the first render already knows whether a user is signed in.
const bootRoute = () => window.NexusAuth.ready.then(route);
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootRoute);
else bootRoute();
