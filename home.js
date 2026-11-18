/* ==========================================================================
   NEXUS INSPECT — Advanced Homepage Motion & Step Indicator
   - Single IntersectionObserver for reveals
   - Single passive scroll handler with rAF loop (no layout queries during scroll)
   - Step indicator (A1–A5): reading line at 40% vh, smooth fade-out/in, monotonic
   - Vertical progress track and step markers (5 markers, scale ~1.2 when active)
   - Global scroll progress bar and hero panel parallax
   - Micro-interactions, count-up, scanner walk-through
   - Reduced-motion support and no-JS fallback
   ========================================================================== */

(function () {
  'use strict';

  const isReduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const STEPS = [
    { num: '01', title: 'Submit a repository' },
    { num: '02', title: 'Scan files against rules' },
    { num: '03', title: 'Correlate and score' },
    { num: '04', title: 'Review with evidence' },
    { num: '05', title: 'Export reports' }
  ];

  let currentIo = null;
  let activeStepIdx = 0;
  let stepTransitionTimer = null;
  let cachedMeasurements = null;
  let resizeTimer = null;
  let scrollTicking = false;
  let scrollRafId = null;

  // Cached DOM element references
  let globalProgressEl = null;
  let heroPanelEl = null;
  let howProgressFillEl = null;
  let currentWrapEl = null;
  let numEl = null;
  let titleEl = null;
  let markerEls = [];

  function getAbsoluteTop(el) {
    let top = 0;
    let cur = el;
    while (cur) {
      top += cur.offsetTop;
      cur = cur.offsetParent;
    }
    return top;
  }

  function updateMeasurements() {
    const howEl = document.getElementById('how');
    const stepEls = Array.from(document.querySelectorAll('.hm-how-step'));
    const heroPanel = document.getElementById('heroPanelWrap');

    if (!howEl || !stepEls.length) return;

    const vh = window.innerHeight;
    const docH = document.documentElement.scrollHeight;
    const maxScroll = Math.max(1, docH - vh);

    const howTop = getAbsoluteTop(howEl);
    const howHeight = howEl.offsetHeight;
    const howBottom = howTop + howHeight;

    const stepTops = stepEls.map((s, idx) => {
      const top = getAbsoluteTop(s);
      return {
        idx,
        top,
        bottom: top + s.offsetHeight
      };
    });

    cachedMeasurements = {
      vh,
      docH,
      maxScroll,
      howTop,
      howHeight,
      howBottom,
      stepTops,
      hasHeroPanel: !!heroPanel
    };
  }

  function setActiveStep(idx, immediate = false) {
    if (idx < 0 || idx >= STEPS.length || idx === activeStepIdx) return;
    activeStepIdx = idx;
    const target = STEPS[idx];

    // Update marker classes immediately
    if (markerEls && markerEls.length) {
      markerEls.forEach((m, mIdx) => {
        m.classList.toggle('filled', mIdx < idx);
        m.classList.toggle('active', mIdx === idx);
      });
    }

    if (immediate || isReduced() || !currentWrapEl || !numEl || !titleEl) {
      if (numEl) numEl.textContent = target.num;
      if (titleEl) titleEl.textContent = target.title;
      return;
    }

    if (stepTransitionTimer) {
      clearTimeout(stepTransitionTimer);
      stepTransitionTimer = null;
    }

    // Step transition (A3):
    // 1. Old numeral and title fade out (opacity 1 -> 0, translateY 0 -> -12px) over 280ms
    currentWrapEl.classList.remove('step-in-prep');
    currentWrapEl.classList.add('step-out');

    // 2. Starting 120ms after fade-out begins, update content and fade in (opacity 0 -> 1, translateY 14px -> 0) over 520ms
    stepTransitionTimer = setTimeout(() => {
      numEl.textContent = target.num;
      titleEl.textContent = target.title;
      currentWrapEl.classList.remove('step-out');
      currentWrapEl.classList.add('step-in-prep');
      void currentWrapEl.offsetHeight; // Force layout flush so prep takes effect with transition: none
      currentWrapEl.classList.remove('step-in-prep');
    }, 120);
  }

  function handleScrollFrame() {
    scrollTicking = false;
    if (!cachedMeasurements) updateMeasurements();
    if (!cachedMeasurements) return;

    const scrollY = window.scrollY;
    const { vh, maxScroll, howTop, howBottom, stepTops, hasHeroPanel } = cachedMeasurements;

    // 1. Top Global Scroll Progress (scaleX)
    if (globalProgressEl) {
      const gp = Math.max(0, Math.min(1, scrollY / maxScroll));
      globalProgressEl.style.transform = `scaleX(${gp.toFixed(3)})`;
    }

    // 2. Hero Panel Parallax (translate3d clamped to 40px)
    if (hasHeroPanel && heroPanelEl && !isReduced()) {
      const heroY = Math.min(40, Math.max(0, scrollY * 0.06));
      heroPanelEl.style.transform = `translate3d(0, ${heroY.toFixed(2)}px, 0)`;
    }

    // 3. How It Works Section Progress & Step Detection
    const sStart = howTop;
    const sEnd = howBottom - vh;

    if (howProgressFillEl) {
      let p = 0;
      if (scrollY <= sStart) {
        p = 0;
      } else if (scrollY >= sEnd) {
        p = 1;
      } else if (sEnd > sStart) {
        p = (scrollY - sStart) / (sEnd - sStart);
      }
      p = Math.max(0, Math.min(1, Math.round(p * 1000) / 1000));
      howProgressFillEl.style.transform = `scaleY(${p.toFixed(3)})`;
    }

    // Active Step Detection at Reading Line (40% Viewport Height)
    const readingLine = scrollY + vh * 0.4;
    let currentStep = 0;
    for (let i = 0; i < stepTops.length; i++) {
      if (readingLine >= stepTops[i].top) {
        currentStep = i;
      }
    }
    setActiveStep(currentStep);
  }

  function onScroll() {
    if (!scrollTicking) {
      scrollTicking = true;
      scrollRafId = requestAnimationFrame(handleScrollFrame);
    }
  }

  function initReveal() {
    if (currentIo) {
      currentIo.disconnect();
      currentIo = null;
    }

    const items = document.querySelectorAll('.reveal');
    if (!items.length || !('IntersectionObserver' in window)) {
      items.forEach((el) => el.classList.add('is-in'));
      return;
    }

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-in');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -8% 0px' });

    currentIo = io;

    // Card & element stagger: 120ms each, capped at 6 (B4)
    const groups = new Map();
    items.forEach((el) => {
      const parent = el.parentElement;
      const i = groups.get(parent) || 0;
      groups.set(parent, i + 1);
      el.style.setProperty('--d', Math.min(i, 5) * 120 + 'ms');
      io.observe(el);
    });
  }

  function initCountUp() {
    const countEls = document.querySelectorAll('.count-up');
    if (!countEls.length) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          io.unobserve(entry.target);
          const el = entry.target;
          const target = parseFloat(el.dataset.target || el.textContent) || 0;
          if (isReduced()) {
            el.textContent = target;
            return;
          }
          const start = performance.now();
          const duration = 1100;
          function update(now) {
            const elapsed = now - start;
            const progress = Math.min(1, elapsed / duration);
            const ease = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(ease * target);
            el.textContent = current;
            if (progress < 1) {
              requestAnimationFrame(update);
            } else {
              el.textContent = target;
            }
          }
          requestAnimationFrame(update);
        }
      });
    }, { threshold: 0.2 });
  }

  // Illustrative sample data for scanner walkthrough panel
  const SAMPLE = {
    files: [
      { path: 'config/settings.js', flagged: true, lang: 'JavaScript' },
      { path: 'src/db/users.js', flagged: true, lang: 'JavaScript' },
      { path: 'src/routes/admin.js', flagged: true, lang: 'JavaScript' },
      { path: 'src/services/payments.js', flagged: true, lang: 'JavaScript' },
      { path: 'src/app.js', flagged: true, lang: 'JavaScript' },
      { path: 'package.json', flagged: false, lang: 'Config' }
    ],
    code: {
      'config/settings.js': [
        "const settings = {",
        "  api_key: 'sk_t******** (length 24)',",
        "  debug: true,",
        "};"
      ],
      'src/db/users.js': [
        "export function findUser(email) {",
        "  return db.query(\"SELECT * FROM users WHERE email = '\" + email + \"'\");",
        "}"
      ],
      'src/routes/admin.js': [
        "router.get('/admin/export', exportUsers);",
        "router.post('/admin/reset', resetAll);"
      ],
      'src/services/payments.js': [
        "export async function charge(order) {",
        "  return { ok: true }; // stub: no provider call is made",
        "}"
      ],
      'src/app.js': [
        "// TODO: wire the retry policy before launch",
        "app.listen(process.env.PORT || 8000);"
      ],
      'package.json': ['{ "dependencies": { "left-pad": "1.3.0" } }']
    },
    findings: [
      { id: 'SEC-SECRET-001', sev: 'critical', cat: 'Security', title: 'Hardcoded credential in source or configuration', file: 'config/settings.js', line: 2,
        why: 'Anyone with read access to the repository or built bundle can reuse the credential.',
        fix: 'Move the value to a server-side environment variable or secret manager.' },
      { id: 'SEC-SQL-001', sev: 'high', cat: 'Security', title: 'SQL statement built by string concatenation', file: 'src/db/users.js', line: 2,
        why: 'Untrusted input placed into the query text can alter SQL execution (SQL injection).',
        fix: 'Use parameterized queries or a query builder; never join user input into SQL strings.' },
      { id: 'SEC-AUTHZ-001', sev: 'high', cat: 'Security', title: 'Sensitive route registered without authorization guard', file: 'src/routes/admin.js', line: 2,
        why: 'Administrative endpoints reachable by any caller expose actions to unauthorized users.',
        fix: 'Attach role middleware and verify unprivileged callers receive HTTP 403.' },
      { id: 'SLOP-STUB-001', sev: 'high', cat: 'AI slop', title: 'Function returns success without performing its operation', file: 'src/services/payments.js', line: 2,
        why: 'Callers receive a success response for a critical operation that never happened.',
        fix: 'Implement the real provider call or fail explicitly until it exists.' },
      { id: 'LOOSE-TODO-001', sev: 'low', cat: 'Loose ends', title: 'Unresolved TODO / FIXME marker left in code', file: 'src/app.js', line: 1,
        why: 'Unfinished work is shipped without a visible owner or tracking item.',
        fix: 'Complete the implementation or record it in the issue tracker.' }
    ]
  };

  let scanTimers = [];
  function clearScanTimers() {
    scanTimers.forEach(clearTimeout);
    scanTimers = [];
  }

  function initScanDemo() {
    const stage = document.getElementById('showStage');
    const tree = document.getElementById('showTree');
    const codeEl = document.getElementById('showCode');
    const fileEl = document.getElementById('showFile');
    const listEl = document.getElementById('showList');
    const detailEl = document.getElementById('showDetail');
    const countEl = document.getElementById('showCount');
    const countsEl = document.getElementById('showCounts');
    const scanline = document.getElementById('showScanline');
    const replayBtn = document.getElementById('showReplay');

    if (!stage || !tree || !codeEl) return;
    clearScanTimers();

    const SEV_ORDER = ['critical', 'high', 'medium', 'low'];
    let currentFindings = [];

    function renderTree(scanned) {
      tree.innerHTML = SAMPLE.files.map((f) =>
        `<li class="hm-file${f.flagged ? ' flagged' : ''}${scanned.has(f.path) ? ' scanned' : ''}" data-path="${esc(f.path)}">
          <span class="hm-dot" aria-hidden="true"></span>
          <span>${esc(f.path)}</span>
        </li>`
      ).join('');
    }

    function showFileContent(path, hitLine = null) {
      if (fileEl) fileEl.textContent = path;
      const lines = SAMPLE.code[path] || [];
      codeEl.innerHTML = lines.map((text, i) => {
        const n = i + 1;
        const hit = hitLine === n;
        return `<span class="hm-ln${hit ? ' hit' : ''}"><span class="hm-no">${n}</span>${esc(text)}</span>`;
      }).join('\n');
      tree.querySelectorAll('.hm-file').forEach((li) => li.classList.toggle('active', li.dataset.path === path));
    }

    function selectFinding(f) {
      listEl.querySelectorAll('.hm-item').forEach((li) => li.classList.toggle('active', li.dataset.id === f.id));
      showFileContent(f.file, f.line);
      if (detailEl) {
        detailEl.innerHTML = `<p class="hm-why"><strong>${esc(f.title)}</strong></p><p>${esc(f.why)}</p><p class="hm-fix">Fix: ${esc(f.fix)}</p>`;
      }
    }

    function updateCounts(n) {
      const by = { critical: 0, high: 0, medium: 0, low: 0 };
      currentFindings.forEach((f) => { if (by[f.sev] !== undefined) by[f.sev]++; });
      if (countsEl) {
        countsEl.innerHTML = SEV_ORDER.map((s) => `<span class="sev ${s}">${by[s]}</span>`).join('');
      }
      if (countEl) countEl.textContent = n;
    }

    function populateSettled() {
      clearScanTimers();
      currentFindings = [...SAMPLE.findings];
      const scanned = new Set(SAMPLE.files.map((f) => f.path));
      renderTree(scanned);
      updateCounts(currentFindings.length);
      listEl.innerHTML = currentFindings.map((f) =>
        `<li class="hm-item" data-id="${esc(f.id)}">
          <span class="sev ${esc(f.sev)}">${esc(f.sev)}</span>
          <span class="hm-item-title">${esc(f.title)}</span>
          <span class="hm-item-file">${esc(f.file)}:${f.line}</span>
        </li>`
      ).join('');
      if (currentFindings.length > 0) selectFinding(currentFindings[0]);
      stage.dataset.state = 'complete';
      if (scanline) scanline.classList.remove('scanning');
    }

    function play() {
      clearScanTimers();
      currentFindings = [];
      listEl.innerHTML = '';
      if (detailEl) detailEl.innerHTML = '<p class="muted">Scanning repository files...</p>';
      updateCounts(0);
      const scanned = new Set();
      renderTree(scanned);

      if (isReduced()) {
        populateSettled();
        return;
      }

      stage.dataset.state = 'running';
      if (scanline) scanline.classList.add('scanning');

      SAMPLE.files.forEach((file, fileIdx) => {
        const fileTimer = setTimeout(() => {
          scanned.add(file.path);
          renderTree(scanned);
          showFileContent(file.path);

          const hits = SAMPLE.findings.filter((f) => f.file === file.path);
          hits.forEach((hit, hitIdx) => {
            const hitTimer = setTimeout(() => {
              showFileContent(file.path, hit.line);
              currentFindings.push(hit);
              updateCounts(currentFindings.length);

              const li = document.createElement('li');
              li.className = 'hm-item';
              li.dataset.id = hit.id;
              li.innerHTML = `<span class="sev ${esc(hit.sev)}">${esc(hit.sev)}</span><span class="hm-item-title">${esc(hit.title)}</span><span class="hm-item-file">${esc(hit.file)}:${hit.line}</span>`;
              listEl.appendChild(li);
              selectFinding(hit);
            }, (hitIdx + 1) * 300);
            scanTimers.push(hitTimer);
          });
        }, fileIdx * 500);
        scanTimers.push(fileTimer);
      });

      const finishTimer = setTimeout(() => {
        stage.dataset.state = 'complete';
        if (scanline) scanline.classList.remove('scanning');
      }, SAMPLE.files.length * 500 + 400);
      scanTimers.push(finishTimer);
    }

    if (replayBtn) {
      replayBtn.onclick = () => play();
    }

    tree.onclick = (e) => {
      const li = e.target.closest('.hm-file');
      if (!li) return;
      const path = li.dataset.path;
      showFileContent(path);
      const hit = currentFindings.find((f) => f.file === path);
      if (hit) selectFinding(hit);
    };

    listEl.onclick = (e) => {
      const li = e.target.closest('.hm-item');
      if (!li) return;
      const hit = currentFindings.find((f) => f.id === li.dataset.id);
      if (hit) selectFinding(hit);
    };

    populateSettled();
  }

  /* ---- Section transitions -------------------------------------------------
     Each section boundary gets a hairline that draws across as the section arrives. Its progress is
     scroll-scrubbed (one scroll frame, reads before writes, writes only on change), so it stops when the page is still.
     A faint light rises behind each section once it enters. Transform and opacity only; the sticky How-it-works
     column is never inside a transformed ancestor. */
  const SECTION_SELECTOR = '.hm-statement-section, .hm-section, .hm-foot';
  let sectionEls = [];
  let sectionIo = null;
  let seamRafId = null;
  let seamValues = new Map();

  function updateSeams() {
    seamRafId = null;
    const vh = window.innerHeight || 1;
    const tops = sectionEls.map((el) => el.getBoundingClientRect().top); // read phase
    sectionEls.forEach((el, i) => {                                     // write phase
      // 0 when the boundary is at the bottom edge of the viewport, 1 once it has risen 45% of the viewport height
      let p = (vh - tops[i]) / (vh * 0.45);
      p = Math.min(1, Math.max(0, p));
      p = Math.round(p * 1000) / 1000;
      if (seamValues.get(el) === p) return;
      seamValues.set(el, p);
      el.style.setProperty('--seam', String(p));
    });
  }

  function onSectionScroll() {
    if (seamRafId === null) seamRafId = requestAnimationFrame(updateSeams);
  }

  function cleanupSectionBoundaries() {
    if (sectionIo) { sectionIo.disconnect(); sectionIo = null; }
    if (seamRafId !== null) { cancelAnimationFrame(seamRafId); seamRafId = null; }
    window.removeEventListener('scroll', onSectionScroll);
    window.removeEventListener('resize', onSectionScroll);
    seamValues = new Map();
    sectionEls = [];
  }

  function initSectionBoundaries() {
    cleanupSectionBoundaries();
    sectionEls = Array.from(document.querySelectorAll(SECTION_SELECTOR));
    if (!sectionEls.length) return;

    if (isReduced() || !('IntersectionObserver' in window)) {
      // Final state immediately: full hairlines, lights settled. Nothing animates.
      sectionEls.forEach((el) => { el.style.setProperty('--seam', '1'); el.classList.add('sec-in'); });
      return;
    }

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('sec-in');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0, rootMargin: '0px 0px -12% 0px' });
    sectionIo = io;
    sectionEls.forEach((el) => io.observe(el));

    window.addEventListener('scroll', onSectionScroll, { passive: true });
    window.addEventListener('resize', onSectionScroll, { passive: true });
    updateSeams();
  }

  /* ---------- Repository investigation workspace (illustrative sample) ---------- */
  let invWs = null;

  function invSelectFile(file, line) {
    if (!invWs) return;
    invWs.querySelectorAll('pre.inv-code').forEach((pre) => {
      const show = pre.dataset.file === file;
      if (show === pre.hidden) pre.hidden = !show;
      pre.querySelectorAll('.inv-line.is-focus').forEach((el) => el.classList.remove('is-focus'));
      if (show && line) {
        const target = pre.querySelector('.inv-line[data-n="' + line + '"]');
        if (target) target.classList.add('is-focus');
      }
    });
    const path = document.getElementById('invPath');
    if (path) path.textContent = file;
    invWs.querySelectorAll('.inv-file').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.file === file));
    });
  }

  function invSelectFinding(id) {
    if (!invWs) return;
    const finding = invWs.querySelector('.inv-finding[data-finding="' + id + '"]');
    if (!finding) return;
    invWs.querySelectorAll('.inv-finding').forEach((el) => {
      const show = el === finding;
      if (show === el.hidden) el.hidden = !show;
    });
    invWs.querySelectorAll('.inv-fbtn').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.finding === id));
    });
    invSelectFile(finding.dataset.file, Number(finding.dataset.line) || null);
  }

  function onInvClick(event) {
    const target = event.target.closest('button');
    if (!target || !invWs || !invWs.contains(target)) return;
    if (target.dataset.finding) {
      invSelectFinding(target.dataset.finding);
    } else if (target.classList.contains('inv-file')) {
      const finding = invWs.querySelector('.inv-finding[data-file="' + target.dataset.file + '"]');
      if (finding) invSelectFinding(finding.dataset.finding);
      else invSelectFile(target.dataset.file, null);
    } else if (target.dataset.file) {
      invSelectFile(target.dataset.file, Number(target.dataset.line) || null);
    }
  }

  function initInvestigation() {
    cleanupInvestigation();
    invWs = document.getElementById('invWorkspace');
    if (invWs) invWs.addEventListener('click', onInvClick);
  }

  function cleanupInvestigation() {
    if (invWs) invWs.removeEventListener('click', onInvClick);
    invWs = null;
  }

  function initHome() {
    globalProgressEl = document.getElementById('globalScrollProgress');
    heroPanelEl = document.getElementById('heroPanelWrap');
    howProgressFillEl = document.getElementById('howProgressFill');
    currentWrapEl = document.querySelector('.hm-how-current');
    numEl = document.getElementById('howActiveNum');
    titleEl = document.getElementById('howActiveTitle');
    markerEls = Array.from(document.querySelectorAll('.hm-how-marker'));

    initReveal();
    initInvestigation();
    initSectionBoundaries();
    initCountUp();
    initScanDemo();

    updateMeasurements();
    if (document.fonts) {
      document.fonts.ready.then(updateMeasurements);
    }

    window.removeEventListener('scroll', onScroll);
    window.addEventListener('scroll', onScroll, { passive: true });

    window.removeEventListener('resize', onResize);
    window.addEventListener('resize', onResize, { passive: true });

    handleScrollFrame();
  }

  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      updateMeasurements();
      handleScrollFrame();
    }, 150);
  }

  function cleanup() {
    clearScanTimers();
    if (stepTransitionTimer) {
      clearTimeout(stepTransitionTimer);
      stepTransitionTimer = null;
    }
    if (scrollRafId) {
      cancelAnimationFrame(scrollRafId);
      scrollRafId = null;
    }
    if (currentIo) {
      currentIo.disconnect();
      currentIo = null;
    }
    cleanupSectionBoundaries();
    cleanupInvestigation();
    window.removeEventListener('scroll', onScroll);
    window.removeEventListener('resize', onResize);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHome);
  } else {
    initHome();
  }

  window.NexusHome = {
    init: initHome,
    cleanup: cleanup,
    updateMeasurements: updateMeasurements
  };
})();
