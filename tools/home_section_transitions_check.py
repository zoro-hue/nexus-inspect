"""Measured check for homepage section transitions (real Chromium via Playwright).

Usage: python tools/home_section_transitions_check.py http://127.0.0.1:8000
Prints a JSON report and exits non-zero on any failed check.
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = os.environ.get("SHOTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "screenshots"))
os.makedirs(SHOTS, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)})


SEAM_JS = """(el) => { const t = getComputedStyle(el, '::before').transform; return t; }"""
SEC_IN_JS = "() => document.querySelectorAll('.hm-statement-section.sec-in, .hm-section.sec-in, .hm-foot').length"
TOTAL_JS = "() => document.querySelectorAll('.hm-statement-section, .hm-section, .hm-foot').length"
PINNED_JS = "() => { const e = document.querySelector('.hm-how-pinned'); return e ? getComputedStyle(e).position : null; }"
ANIMS_JS = "() => document.getAnimations().filter(a => a.playState === 'running').length"


def seam_scale(page, selector_index):
    matrix = page.evaluate(
        "(i) => getComputedStyle(document.querySelectorAll('.hm-statement-section, .hm-section, .hm-foot')[i], '::before').transform",
        selector_index,
    )
    if matrix in (None, "none", ""):
        return 0.0
    vals = [float(x) for x in matrix.replace("matrix(", "").replace(")", "").split(",")]
    return vals[0]


def scroll_to_section(page, index, fraction):
    page.evaluate(
        """([i, f]) => { const el = document.querySelectorAll('.hm-statement-section, .hm-section, .hm-foot')[i];
             const y = el.getBoundingClientRect().top + window.scrollY - window.innerHeight * f;
             window.scrollTo({ top: Math.max(0, y), behavior: 'instant' }); }""",
        [index, fraction],
    )


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        csp = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("console", lambda m: csp.append(m.text) if "Content Security Policy" in m.text else None)
        page.goto(BASE + "/#home", wait_until="load")
        page.wait_for_timeout(600)

        total = page.evaluate(TOTAL_JS)
        check("boundary elements found", total >= 8, total)

        # 1. Hairline draws with scroll: before the boundary is in view it is hidden, mid-way partial, at the top full.
        scroll_to_section(page, 2, 1.25)  # boundary just below the bottom edge
        page.wait_for_timeout(500)
        s_hidden = seam_scale(page, 2)
        check("hairline hidden before boundary enters", s_hidden < 0.05, round(s_hidden, 3))

        partial_seen = False
        full_seen = False
        for f in [1.0, 0.8, 0.65, 0.55, 0.45]:
            scroll_to_section(page, 2, f)
            page.wait_for_timeout(480)
            v = seam_scale(page, 2)
            if 0.15 < v < 0.9:
                partial_seen = True
            if v > 0.97:
                full_seen = True
        check("hairline passes through partial values while scrolling", partial_seen)
        check("hairline reaches full width when section is well in view", full_seen)

        # 2. Light rises on entry and the section is marked.
        page.wait_for_timeout(1600)
        sec_in = page.evaluate(SEC_IN_JS)
        check("sections get sec-in after entering", sec_in >= 4, sec_in)
        opacity_after = page.evaluate(
            "() => getComputedStyle(document.querySelectorAll('.hm-section.sec-in')[0] || document.body, '::after').opacity")
        check("entry light fully visible after settling", float(opacity_after or 0) > 0.95, opacity_after)

        # 3. Idle stillness: no running animations and no transform change after settling.
        page.wait_for_timeout(1200)
        samples = []
        for _ in range(6):
            samples.append(page.evaluate(ANIMS_JS))
            page.wait_for_timeout(250)
        check("no running animations while idle", max(samples) == 0, samples)

        # 4. Sticky How-it-works column still sticks while its steps scroll.
        pinned = page.evaluate(PINNED_JS)
        check("how-it-works column still position: sticky", pinned == "sticky", pinned)
        how = page.query_selector("#how")
        how.scroll_into_view_if_needed()
        tops = []
        for f in [-0.3, -0.6, -0.9]:
            scroll_to_section(page, page.evaluate("() => [...document.querySelectorAll('.hm-statement-section, .hm-section, .hm-foot')].indexOf(document.getElementById('how'))"), f)
            page.wait_for_timeout(300)
            tops.append(page.evaluate("() => Math.round(document.querySelector('.hm-how-pinned').getBoundingClientRect().top)"))
        check("sticky column pins at its 120px offset while steps scroll", all(118 <= t <= 122 for t in tops), tops)

        # 5. Screenshot of a boundary mid-transition for review.
        scroll_to_section(page, 4, 0.7)
        page.wait_for_timeout(120)
        page.screenshot(path=os.path.join(SHOTS, "section_transition_mid_1440.png"))

        # 6. Console and CSP.
        check("no console errors", not errors, errors[:3])
        check("no CSP violations", not csp, csp[:3])

        # 7. Reduced motion: final state immediately, no animations.
        rctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        rp = rctx.new_page()
        rp.goto(BASE + "/#home", wait_until="load")
        rp.wait_for_timeout(400)
        rseam = [seam_scale(rp, i) for i in range(2, 6)]
        check("reduced motion: hairlines at full width", all(v > 0.99 for v in rseam), rseam)
        check("reduced motion: no running animations", rp.evaluate(ANIMS_JS) == 0, rp.evaluate(ANIMS_JS))
        rctx.close()

        # 8. Mobile: no horizontal overflow.
        mctx = browser.new_context(viewport={"width": 390, "height": 844})
        mp = mctx.new_page()
        mp.goto(BASE + "/#home", wait_until="load")
        mp.wait_for_timeout(400)
        overflow = mp.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("390px: no horizontal overflow", overflow <= 0, overflow)
        mctx.close()
        browser.close()


run()
passed = sum(1 for r in results if r["ok"])
report = {"passed": passed, "failed": len(results) - passed, "checks": results}
print(json.dumps(report, indent=2))
sys.exit(0 if report["failed"] == 0 else 1)
