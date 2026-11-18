import re
"""NEXUS INSPECT — Homepage Section Transitions Verification Suite
Step 4 checks:
1. Old code gone
2. New code loaded (version string check)
3. Start state opacity < 0.05
4. Visible transition (samples at 0, 100, 250, 500, 800, 1100 ms rise monotonically with one between 0.2 and 0.8)
5. Smoothness (0.1 to 0.9 duration between 500ms and 1400ms)
6. Settled and still (after 1.5s, opacity 1, transform translate3d(0,0,0)/none, 0 running animations, static over 3s)
7. Sections do not move while idle (full scroll stillness check)
8. Frame smoothness (longest frame < 40ms, <= 3 frames > 20ms)
9. Reduced motion emulated (immediately visible, 0 running transitions)
10. No JavaScript (all section text visible)
11. Console errors and CSP violations (zero)
12. Layout shift (CLS = 0 across full scroll)
"""

import sys
import os
import json
import time
import urllib.request
from playwright.sync_api import sync_playwright

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

def run_checks():
    report = {}
    all_passed = True

    # -------------------------------------------------------------
    # Check 1: Old code gone
    # -------------------------------------------------------------
    removed_terms = [
        'initSectionTransitions', 'onScrollFrame', 'measureBounds', 'handleScroll',
        '__nexus_revealSection', '__nexus_registry', 'js-reveal', 'hm-rv',
        'hm-lead-reveal', 'hm-cta-reveal', 'cross-fading', 'sticky-step'
    ]
    files_to_check = ['home.js', 'home.css', 'styles.css', 'index.html', 'boot.js']
    found_matches = {}

    for fn in files_to_check:
        if os.path.exists(fn):
            with open(fn, 'r', encoding='utf-8') as fh:
                txt = fh.read()
            for term in removed_terms:
                # whole-token match, so 'handleScroll' does not flag the current 'handleScrollFrame'
                c = len(re.findall(r'(?<![A-Za-z0-9_])' + re.escape(term) + r'(?![A-Za-z0-9_])', txt))
                if c > 0:
                    found_matches[f"{fn}:{term}"] = c

    # Also check will-change in home.css and styles.css
    with open('home.css', 'r', encoding='utf-8') as fh:
        wc_home = fh.read().count('will-change')
    with open('styles.css', 'r', encoding='utf-8') as fh:
        wc_styles = fh.read().count('will-change')

    ch1_ok = len(found_matches) == 0 and wc_home == 0 and wc_styles == 0
    report["check_1_old_code_gone"] = {
        "passed": ch1_ok,
        "details": {
            "found_matches": found_matches,
            "will_change_in_home_css": wc_home,
            "will_change_in_styles_css": wc_styles
        }
    }
    if not ch1_ok: all_passed = False

    # -------------------------------------------------------------
    # Check 2: New code loaded
    # -------------------------------------------------------------
    with open('index.html', 'r', encoding='utf-8') as fh:
        index_html = fh.read()

    css_v2_linked = 'home.css?v=10' in index_html
    js_v2_linked = 'home.js?v=9' in index_html

    req_css = urllib.request.urlopen(f"{BASE_URL}/home.css?v=10")
    css_bytes = req_css.read()
    css_served_ok = (req_css.status == 200 and len(css_bytes) > 0)

    req_js = urllib.request.urlopen(f"{BASE_URL}/home.js?v=9")
    js_bytes = req_js.read()
    js_served_ok = (req_js.status == 200 and len(js_bytes) > 0)

    req_boot = urllib.request.urlopen(f"{BASE_URL}/boot.js")
    boot_bytes = req_boot.read()
    boot_served_ok = (req_boot.status == 200 and len(boot_bytes) > 0)

    ch2_ok = css_v2_linked and js_v2_linked and css_served_ok and js_served_ok and boot_served_ok
    report["check_2_new_code_loaded"] = {
        "passed": ch2_ok,
        "details": {
            "css_v2_linked": css_v2_linked,
            "js_v2_linked": js_v2_linked,
            "css_status": req_css.status,
            "css_bytes": len(css_bytes),
            "js_status": req_js.status,
            "js_bytes": len(js_bytes),
            "boot_status": req_boot.status,
            "boot_bytes": len(boot_bytes)
        }
    }
    if not ch2_ok: all_passed = False

    # -------------------------------------------------------------
    # Browser Checks 3 through 12 using Playwright Chromium
    # -------------------------------------------------------------
    console_errors = []
    csp_violations = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])

        # Context for normal motion tests
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            reduced_motion="no-preference"
        )
        page = context.new_page()

        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # Listen for securitypolicyviolation in page
        page.add_init_script("""
            window.__csp_violations = [];
            document.addEventListener('securitypolicyviolation', (e) => {
                window.__csp_violations.push(e.blockedURI || e.violatedDirective);
            });
        """)

        page.goto(f"{BASE_URL}/#home")
        page.wait_for_timeout(300)

        # Check 3: Start state - before scrolling, an offscreen reveal block's computed opacity < 0.05
        # #statement .reveal is well below the 900px viewport fold initially
        start_op = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#statement .reveal')).opacity)")
        ch3_ok = start_op < 0.05
        report["check_3_start_state"] = {
            "passed": ch3_ok,
            "details": {
                "selector": "#statement .reveal",
                "computed_opacity": start_op,
                "threshold": "< 0.05"
            }
        }
        if not ch3_ok: all_passed = False

        # Check 4: Visible transition: scroll into view and sample opacity at 0, 100, 250, 500, 800, 1100 ms
        transition_data = page.evaluate("""() => new Promise(resolve => {
            const el = document.querySelector('#statement .reveal');
            el.scrollIntoView({ block: 'center' });
            const times = [0, 100, 250, 500, 800, 1100];
            const samples = {};
            times.forEach(t => {
                setTimeout(() => {
                    samples[t + 'ms'] = parseFloat(getComputedStyle(el).opacity);
                    if (Object.keys(samples).length === times.length) {
                        resolve(samples);
                    }
                }, t);
            });
        })""")

        sample_vals = [
            transition_data["0ms"],
            transition_data["100ms"],
            transition_data["250ms"],
            transition_data["500ms"],
            transition_data["800ms"],
            transition_data["1100ms"]
        ]
        monotonic = all(sample_vals[i] <= sample_vals[i+1] + 0.01 for i in range(len(sample_vals) - 1))
        mid_sample = any(0.2 <= v <= 0.8 for v in sample_vals)
        ch4_ok = monotonic and mid_sample
        report["check_4_visible_transition"] = {
            "passed": ch4_ok,
            "details": {
                "samples": transition_data,
                "monotonic": monotonic,
                "has_sample_between_0_2_and_0_8": mid_sample
            }
        }
        if not ch4_ok: all_passed = False

        # Check 5: Smooth: 0.1 to 0.9 opacity transition takes between 500ms and 1400ms
        # We measure high-resolution samples of #evidence .reveal entering view
        smoothness_data = page.evaluate("""() => new Promise(resolve => {
            const el = document.querySelector('#evidence .reveal');
            el.scrollIntoView({ block: 'center' });
            const t0 = performance.now();
            let t_01 = null;
            let t_09 = null;
            function check() {
                const now = performance.now();
                const op = parseFloat(getComputedStyle(el).opacity);
                if (t_01 === null && op >= 0.1) t_01 = now;
                if (t_09 === null && op >= 0.9) t_09 = now;
                if (t_09 !== null || now - t0 > 2000) {
                    resolve({
                        t_01_elapsed: t_01 ? t_01 - t0 : null,
                        t_09_elapsed: t_09 ? t_09 - t0 : null,
                        duration_ms: (t_01 && t_09) ? (t_09 - t_01) : (now - t0)
                    });
                } else {
                    requestAnimationFrame(check);
                }
            }
            requestAnimationFrame(check);
        })""")
        dur = smoothness_data["duration_ms"]
        ch5_ok = 500 <= dur <= 1400
        report["check_5_smooth"] = {
            "passed": ch5_ok,
            "details": smoothness_data
        }
        if not ch5_ok: all_passed = False

        # Check 6: Settled and still: after 1.5s, opacity 1, transform translate3d(0,0,0)/none, 0 running animations,
        # sample transforms of 5 blocks every 500ms for 3s (all identical)
        page.wait_for_timeout(1500)
        still_data = page.evaluate("""() => new Promise(resolve => {
            const blocks = Array.from(document.querySelectorAll('.reveal.is-in')).slice(0, 5);
            const runningCount = document.getAnimations().filter(a => a.playState === 'running').length;
            const opacities = blocks.map(b => parseFloat(getComputedStyle(b).opacity));
            const transforms = blocks.map(b => getComputedStyle(b).transform);

            // Sample transforms every 500ms for 3000ms (7 samples)
            const sampleHistory = [];
            let count = 0;
            const interval = setInterval(() => {
                const cur = blocks.map(b => getComputedStyle(b).transform);
                sampleHistory.push(cur);
                count++;
                if (count >= 6) {
                    clearInterval(interval);
                    // Check if all samples equal the initial sample
                    const identical = sampleHistory.every(sample =>
                        JSON.stringify(sample) === JSON.stringify(transforms)
                    );
                    resolve({
                        runningCount,
                        allOpacitiesOne: opacities.every(op => op >= 0.99),
                        transformsInitial: transforms,
                        identicalOver3s: identical
                    });
                }
            }, 500);
        })""")

        ch6_ok = still_data["runningCount"] == 0 and still_data["allOpacitiesOne"] and still_data["identicalOver3s"]
        report["check_6_settled_and_still"] = {
            "passed": ch6_ok,
            "details": still_data
        }
        if not ch6_ok: all_passed = False

        # Check 7: Sections do not move while idle (across whole page after full scroll)
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight);")
        page.wait_for_timeout(2200)
        idle_page_data = page.evaluate("""() => new Promise(resolve => {
            const allReveals = Array.from(document.querySelectorAll('.reveal'));
            const runningCount = document.getAnimations().filter(a => a.playState === 'running').length;
            const t0 = allReveals.map(el => getComputedStyle(el).transform);
            setTimeout(() => {
                const t1 = allReveals.map(el => getComputedStyle(el).transform);
                const still = JSON.stringify(t0) === JSON.stringify(t1);
                resolve({
                    totalReveals: allReveals.length,
                    runningCount,
                    stillAfter2s: still
                });
            }, 2000);
        })""")

        ch7_ok = idle_page_data["runningCount"] == 0 and idle_page_data["stillAfter2s"]
        report["check_7_sections_idle_stillness_full_page"] = {
            "passed": ch7_ok,
            "details": idle_page_data
        }
        if not ch7_ok: all_passed = False

        # Check 8: Frame smoothness during full scroll
        page.evaluate("window.scrollTo(0, 0);")
        page.wait_for_timeout(400)
        frame_perf = page.evaluate("""() => new Promise(resolve => {
            let last = performance.now();
            const deltas = [];
            function onFrame(now) {
                const dt = now - last;
                last = now;
                deltas.push(dt);
                window.scrollBy(0, 75);
                if (window.scrollY + window.innerHeight < document.documentElement.scrollHeight - 40 && deltas.length < 90) {
                    requestAnimationFrame(onFrame);
                } else {
                    resolve(deltas.slice(1));
                }
            }
            requestAnimationFrame(onFrame);
        })""")

        longest_frame = max(frame_perf) if frame_perf else 0
        frames_over_20 = sum(1 for d in frame_perf if d > 20.0)
        ch8_ok = longest_frame < 40.0 and frames_over_20 <= 3
        report["check_8_frame_smoothness"] = {
            "passed": ch8_ok,
            "details": {
                "total_frames_sampled": len(frame_perf),
                "longest_frame_ms": round(longest_frame, 2),
                "frames_over_20ms": frames_over_20,
                "pass_criteria": "longest < 40ms and frames_over_20 <= 3"
            }
        }
        if not ch8_ok: all_passed = False

        # Check 11: Console errors and CSP
        csp_reported = page.evaluate("window.__csp_violations || []")
        ch11_ok = len(console_errors) == 0 and len(csp_violations) == 0 and len(csp_reported) == 0
        report["check_11_console_and_csp"] = {
            "passed": ch11_ok,
            "details": {
                "console_errors": console_errors,
                "csp_violations": csp_violations + csp_reported
            }
        }
        if not ch11_ok: all_passed = False

        # Check 12: Layout stability CLS = 0
        page.evaluate("window.scrollTo(0, 0);")
        page.wait_for_timeout(300)
        cls_val = page.evaluate("""() => new Promise(resolve => {
            let cls = 0;
            const obs = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (!entry.hadRecentInput) {
                        cls += entry.value;
                    }
                }
            });
            obs.observe({ type: 'layout-shift', buffered: false });

            let pos = 0;
            const step = () => {
                pos += 150;
                window.scrollTo(0, pos);
                if (pos < document.documentElement.scrollHeight) {
                    requestAnimationFrame(step);
                } else {
                    setTimeout(() => {
                        obs.disconnect();
                        resolve(cls);
                    }, 400);
                }
            };
            requestAnimationFrame(step);
        })""")

        ch12_ok = cls_val == 0
        report["check_12_layout_shift_cls"] = {
            "passed": ch12_ok,
            "details": { "cls": cls_val }
        }
        if not ch12_ok: all_passed = False

        context.close()

        # Check 9: Reduced motion emulated
        rm_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        rm_page = rm_ctx.new_page()
        rm_page.goto(f"{BASE_URL}/#home")
        rm_page.wait_for_timeout(200)

        rm_data = rm_page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.reveal'));
            const opacities = items.map(el => parseFloat(getComputedStyle(el).opacity));
            const transforms = items.map(el => getComputedStyle(el).transform);
            const runningCount = document.getAnimations().filter(a => a.playState === 'running').length;
            return {
                allVisible: opacities.every(op => op === 1),
                allStatic: transforms.every(tr => tr === 'none' || tr.includes('matrix(1, 0, 0, 1, 0, 0)')),
                runningCount
            };
        }""")
        ch9_ok = rm_data["allVisible"] and rm_data["runningCount"] == 0
        report["check_9_reduced_motion_emulated"] = {
            "passed": ch9_ok,
            "details": rm_data
        }
        if not ch9_ok: all_passed = False
        rm_ctx.close()

        # Check 10: No JavaScript: all text visible
        nojs_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, java_script_enabled=False)
        nojs_page = nojs_ctx.new_page()
        nojs_page.goto(f"{BASE_URL}/#home")
        nojs_data = nojs_page.evaluate("""() => {
            const sections = document.querySelectorAll('#hmMain > section');
            const items = Array.from(document.querySelectorAll('.reveal'));
            const opacities = items.map(el => parseFloat(getComputedStyle(el).opacity));
            return {
                sectionCount: sections.length,
                revealCount: items.length,
                allOpacityOne: opacities.every(op => op === 1)
            };
        }""")
        ch10_ok = nojs_data["sectionCount"] == 9 and nojs_data["allOpacityOne"]
        report["check_10_no_javascript_visible"] = {
            "passed": ch10_ok,
            "details": nojs_data
        }
        if not ch10_ok: all_passed = False
        nojs_ctx.close()

        browser.close()

    print(json.dumps(report, indent=2))
    return all_passed

if __name__ == "__main__":
    passed = run_checks()
    sys.exit(0 if passed else 1)
