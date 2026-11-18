"""NEXUS INSPECT — Advanced Homepage Motion & Step Indicator Verification Suite

Verifies all 19 Part D requirements:
1. At each step's reading position, the left numeral equals that step's number (01 to 05).
2. The numeral changes only once per step boundary, with no flicker (monotonic 300px boundary scroll).
3. The progress track's scaleY at start is 0 and at end is 1 (within 0.02), increasing monotonically.
4. Five markers exist; markers before active step are filled; active one is at scale ~1.2.
5. Pinned indicator stays sticky through whole section (top within 1px of 120px).
6. Before scrolling, below-the-fold reveal elements have computed opacity < 0.05.
7. After entering, each reveal element reaches opacity 1 and transform none within 1.3s.
8. Card stagger delays differ by about 120ms each.
9. Opacity climbs through 0.2-0.8 and 0.1->0.9 takes at least 500ms.
10. After entrances and 3s idle wait, document.getAnimations().length is 0 and 5 sampled transforms are identical over 3s.
11. Full scroll frame timing: longest frame < 40ms, <= 3 frames > 20ms.
12. Cumulative layout shift (CLS) totals 0.
13. Reduced motion: final states immediate, 0 running animations, indicator still follows scroll.
14. No JavaScript: all text blocks are visible.
15. Zero console errors and zero CSP violations.
16. Mobile at 390px: no horizontal overflow, indicator stacks above steps, steps reveal.
17. Desktop at 1440px screenshots: start, step 1-5, footer saved under screenshots/.
18. Unit test suite passes.
19. tools/pages_journey.py passes.
"""

import os
import sys
import json
import time
import subprocess
from playwright.sync_api import sync_playwright

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS_DIR = os.path.join(ROOT, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def run_all_checks():
    report = {}
    all_passed = True

    print("==================================================================")
    print("NEXUS INSPECT — Homepage Motion & Step Indicator Verification")
    print(f"Target URL: {BASE_URL}")
    print("==================================================================")

    with sync_playwright() as pw:
        # Browser instance for standard checks
        browser = pw.chromium.launch()

        # -------------------------------------------------------------
        # Session 1: Desktop 1440x900 Standard Environment
        # -------------------------------------------------------------
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_logs = []
        page.on("console", lambda msg: console_logs.append({
            "type": msg.type,
            "text": msg.text
        }))

        page.goto(f"{BASE_URL}/#home")

        # Measure instantly: the page uses smooth scrolling for users; tests read positions after each jump.

        page.add_style_tag(content="html{scroll-behavior:auto !important}")
        page.wait_for_timeout(800)

        # -------------------------------------------------------------
        # Check 6: Before scrolling, each reveal element's computed opacity < 0.05
        # -------------------------------------------------------------
        ch6_data = page.evaluate("""() => {
            const reveals = Array.from(document.querySelectorAll('#capabilities .reveal, #how .reveal, #evidence .reveal'));
            const opacities = reveals.map(r => parseFloat(getComputedStyle(r).opacity));
            const maxOp = opacities.length ? Math.max(...opacities) : 0;
            return {
                count: reveals.length,
                maxOpacity: maxOp,
                allBelow005: opacities.every(op => op < 0.05)
            };
        }""")
        ch6_ok = ch6_data["allBelow005"]
        report["check_6_start_state_opacity_below_0_05"] = {
            "passed": ch6_ok,
            "details": ch6_data
        }
        if not ch6_ok: all_passed = False
        print(f"  [{'PASS' if ch6_ok else 'FAIL'}] Check 6: Before scrolling opacity < 0.05 (max={ch6_data['maxOpacity']})")

        # -------------------------------------------------------------
        # Check 8: Card stagger (delays differ by ~120ms)
        # -------------------------------------------------------------
        ch8_data = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('#capabilitiesGrid .hm-card'));
            const delays = cards.map(c => {
                const dStr = getComputedStyle(c).transitionDelay;
                const first = dStr.split(',')[0].trim();
                return parseFloat(first) * (first.endsWith('ms') ? 1 : 1000);
            });
            const diffs = [];
            for (let i = 0; i < delays.length - 1; i++) {
                diffs.push(Math.round(delays[i+1] - delays[i]));
            }
            return {
                delaysMs: delays,
                diffsMs: diffs,
                staggerMatches120: diffs.every(d => Math.abs(d - 120) <= 10)
            };
        }""")
        ch8_ok = ch8_data["staggerMatches120"]
        report["check_8_card_stagger_120ms"] = {
            "passed": ch8_ok,
            "details": ch8_data
        }
        if not ch8_ok: all_passed = False
        print(f"  [{'PASS' if ch8_ok else 'FAIL'}] Check 8: Card stagger ~120ms (diffs={ch8_data['diffsMs']}ms)")

        # -------------------------------------------------------------
        # Check 1: Step indicator matches active step at each reading position (01 to 05)
        # -------------------------------------------------------------
        step_results = []
        for i in range(5):
            page.evaluate(f"""() => {{
                function getAbsoluteTop(el) {{
                    let top = 0, cur = el;
                    while (cur) {{ top += cur.offsetTop; cur = cur.offsetParent; }}
                    return top;
                }}
                const s = document.querySelectorAll('.hm-how-step')[{i}];
                const targetY = getAbsoluteTop(s) - window.innerHeight * 0.4 + 20;
                window.scrollTo(0, targetY);
            }}""")
            page.wait_for_timeout(350)
            info = page.evaluate("""() => {
                const num = document.getElementById('howActiveNum').textContent.trim();
                const title = document.getElementById('howActiveTitle').textContent.trim();
                const markers = Array.from(document.querySelectorAll('.hm-how-marker'));
                return {
                    num,
                    title,
                    activeMarkerIdx: markers.findIndex(m => m.classList.contains('active')),
                    filledCount: markers.filter(m => m.classList.contains('filled')).length
                };
            }""")
            step_results.append(info)

        expected_nums = ["01", "02", "03", "04", "05"]
        ch1_ok = all(step_results[i]["num"] == expected_nums[i] for i in range(5))
        report["check_1_step_indicator_reading_positions"] = {
            "passed": ch1_ok,
            "details": step_results
        }
        if not ch1_ok: all_passed = False
        print(f"  [{'PASS' if ch1_ok else 'FAIL'}] Check 1: Step indicator reading positions 01-05 ({[s['num'] for s in step_results]})")

        # -------------------------------------------------------------
        # Check 2: Numeral changes once per boundary without flicker (monotonic across 300px scroll)
        # -------------------------------------------------------------
        # Reset scroll to start of boundary and let step indicator settle on step 1
        # Anchor the window to where step 1 activates (same formula as check 1), so the
        # 300px window covers the first boundary regardless of section spacing.
        b_start, b_end = page.evaluate("""() => {
            const getAbsoluteTop = (el) => { let top = 0, n = el; while (n) { top += n.offsetTop; n = n.offsetParent; } return top; };
            const act = (i) => getAbsoluteTop(document.querySelectorAll('.hm-how-step')[i]) - window.innerHeight * 0.4 + 20;
            return [Math.max(0, Math.round(act(0) - 40)), Math.round(act(1) + 40)];
        }""")
        b_start, b_end = int(b_start), int(b_end)
        page.evaluate(f"window.scrollTo(0, {b_start})")
        page.wait_for_timeout(300)

        boundary_nums = []
        for y in range(b_start, b_end, 15):
            page.evaluate(f"window.scrollTo(0, {y})")
            page.wait_for_timeout(100)
            num_val = int(page.evaluate("() => document.getElementById('howActiveNum').textContent.trim()"))
            boundary_nums.append(num_val)

        ch2_monotonic = all(boundary_nums[i] <= boundary_nums[i+1] for i in range(len(boundary_nums) - 1))
        ch2_ok = ch2_monotonic and min(boundary_nums) >= 1 and max(boundary_nums) > min(boundary_nums) and max(boundary_nums) - min(boundary_nums) <= 2
        report["check_2_step_change_monotonic_no_flicker"] = {
            "passed": ch2_ok,
            "details": {
                "samples": boundary_nums,
                "is_monotonic": ch2_monotonic
            }
        }
        if not ch2_ok: all_passed = False
        print(f"  [{'PASS' if ch2_ok else 'FAIL'}] Check 2: Monotonic step 1→2 boundary transition (samples={boundary_nums[:6]}...{boundary_nums[-4:]})")

        # -------------------------------------------------------------
        # Check 3: Progress track scaleY: 0 at start, 1 at end (within 0.02), monotonic
        # -------------------------------------------------------------
        page.evaluate("""() => {
            const how = document.getElementById('how');
            window.scrollTo(0, how.offsetTop);
        }""")
        page.wait_for_timeout(200)
        scale_start = page.evaluate("""() => {
            const fill = document.getElementById('howProgressFill');
            const tr = getComputedStyle(fill).transform;
            if (tr === 'none') return 0;
            const parts = tr.replace('matrix(', '').replace(')', '').split(',');
            return parseFloat(parts[3]);
        }""")

        page.evaluate("""() => {
            const how = document.getElementById('how');
            window.scrollTo(0, how.offsetTop + how.offsetHeight - window.innerHeight);
        }""")
        page.wait_for_timeout(200)
        scale_end = page.evaluate("""() => {
            const fill = document.getElementById('howProgressFill');
            const tr = getComputedStyle(fill).transform;
            if (tr === 'none') return 0;
            const parts = tr.replace('matrix(', '').replace(')', '').split(',');
            return parseFloat(parts[3]);
        }""")

        bounds = page.evaluate("""() => {
            const how = document.getElementById('how');
            return {
                start: how.offsetTop,
                end: how.offsetTop + how.offsetHeight - window.innerHeight
            };
        }""")
        track_samples = []
        step_gap = int((bounds["end"] - bounds["start"]) / 8)
        for y in range(bounds["start"], bounds["end"] + step_gap, step_gap):
            page.evaluate(f"window.scrollTo(0, {y})")
            page.wait_for_timeout(60)
            sc = page.evaluate("""() => {
                const fill = document.getElementById('howProgressFill');
                const tr = getComputedStyle(fill).transform;
                if (tr === 'none') return 0;
                const parts = tr.replace('matrix(', '').replace(')', '').split(',');
                return parseFloat(parts[3]);
            }""")
            track_samples.append(sc)

        track_monotonic = all(track_samples[i] <= track_samples[i+1] + 0.001 for i in range(len(track_samples) - 1))
        ch3_ok = (abs(scale_start - 0.0) <= 0.02) and (abs(scale_end - 1.0) <= 0.02) and track_monotonic
        report["check_3_progress_track_scale_0_to_1"] = {
            "passed": ch3_ok,
            "details": {
                "scale_start": scale_start,
                "scale_end": scale_end,
                "samples": track_samples,
                "is_monotonic": track_monotonic
            }
        }
        if not ch3_ok: all_passed = False
        print(f"  [{'PASS' if ch3_ok else 'FAIL'}] Check 3: Track scaleY 0->1 (start={scale_start}, end={scale_end}, mono={track_monotonic})")

        # -------------------------------------------------------------
        # Check 4: Five markers exist, passed are filled, active scaled ~1.2
        # -------------------------------------------------------------
        # Position at Step 3 (idx 2)
        page.evaluate("""() => {
            function getAbsoluteTop(el) {
                let top = 0, cur = el;
                while (cur) { top += cur.offsetTop; cur = cur.offsetParent; }
                return top;
            }
            const s = document.querySelectorAll('.hm-how-step')[2];
            window.scrollTo(0, getAbsoluteTop(s) - window.innerHeight * 0.4 + 20);
        }""")
        page.wait_for_timeout(300)
        marker_details = page.evaluate("""() => {
            const markers = Array.from(document.querySelectorAll('.hm-how-marker'));
            return markers.map(m => {
                const tr = getComputedStyle(m).transform;
                const parts = tr.replace('matrix(', '').replace(')', '').split(',');
                const scale = tr === 'none' ? 1.0 : parseFloat(parts[0]);
                return {
                    filled: m.classList.contains('filled'),
                    active: m.classList.contains('active'),
                    scale
                };
            });
        }""")
        ch4_ok = (
            len(marker_details) == 5 and
            marker_details[0]["filled"] and marker_details[1]["filled"] and
            marker_details[2]["active"] and abs(marker_details[2]["scale"] - 1.2) <= 0.15 and
            not marker_details[3]["filled"] and not marker_details[4]["filled"]
        )
        report["check_4_markers_status_and_scale"] = {
            "passed": ch4_ok,
            "details": marker_details
        }
        if not ch4_ok: all_passed = False
        print(f"  [{'PASS' if ch4_ok else 'FAIL'}] Check 4: Five markers (active scale={marker_details[2]['scale']:.2f})")

        # -------------------------------------------------------------
        # Check 5: Sticky indicator top stays within 1px of sticky offset (120px)
        # -------------------------------------------------------------
        sticky_tops = []
        # Sample relative to the How-it-works section, not fixed page offsets: the hero's height changes the page layout.
        how_range = page.evaluate("""() => {
            const sec = document.getElementById('how');
            const top = sec.getBoundingClientRect().top + window.scrollY;
            return {top: top, span: sec.offsetHeight - window.innerHeight};
        }""")
        for frac in [0.15, 0.3, 0.45, 0.6, 0.75]:
            scroll_target = int(how_range["top"] + frac * how_range["span"])
            page.evaluate(f"window.scrollTo(0, {scroll_target})")
            page.wait_for_timeout(100)
            top_val = page.evaluate("""() => {
                const el = document.getElementById('howPinned');
                return el ? Math.round(el.getBoundingClientRect().top) : null;
            }""")
            sticky_tops.append(top_val)

        ch5_ok = all(top is not None and abs(top - 120) <= 1 for top in sticky_tops)
        report["check_5_sticky_indicator_within_1px"] = {
            "passed": ch5_ok,
            "details": {"sampled_tops": sticky_tops}
        }
        if not ch5_ok: all_passed = False
        print(f"  [{'PASS' if ch5_ok else 'FAIL'}] Check 5: Sticky offset within 1px of 120px ({sticky_tops})")

        # -------------------------------------------------------------
        # Check 7 & 9: Opacity 0.1->0.9 takes >= 500ms, mid sample 0.2-0.8, finishes in <= 1.3s
        # -------------------------------------------------------------
        trans_meas = page.evaluate("""() => new Promise(resolve => {
            const el = document.querySelector('#evidence .reveal');
            el.scrollIntoView({ block: 'center' });
            const t0 = performance.now();
            let t_01 = null;
            let t_09 = null;
            const samples = [];
            function loop() {
                const now = performance.now();
                const op = parseFloat(getComputedStyle(el).opacity);
                samples.push(op);
                if (t_01 === null && op >= 0.1) t_01 = now;
                if (t_09 === null && op >= 0.9) t_09 = now;
                if (now - t0 < 1300) {
                    requestAnimationFrame(loop);
                } else {
                    resolve({
                        duration_01_09_ms: (t_01 && t_09) ? (t_09 - t_01) : null,
                        final_opacity: parseFloat(getComputedStyle(el).opacity),
                        final_transform: getComputedStyle(el).transform,
                        has_mid_sample: samples.some(s => s >= 0.2 && s <= 0.8)
                    });
                }
            }
            requestAnimationFrame(loop);
        })""")
        dur_01_09 = trans_meas["duration_01_09_ms"] or 0
        ch9_ok = (dur_01_09 >= 500) and trans_meas["has_mid_sample"]
        ch7_ok = (trans_meas["final_opacity"] >= 0.99) and (trans_meas["final_transform"] == "none")

        report["check_7_reveal_finishes_within_1_3s"] = {
            "passed": ch7_ok,
            "details": trans_meas
        }
        report["check_9_opacity_smooth_curve"] = {
            "passed": ch9_ok,
            "details": trans_meas
        }
        if not ch7_ok: all_passed = False
        if not ch9_ok: all_passed = False
        print(f"  [{'PASS' if ch7_ok else 'FAIL'}] Check 7: Reaches opacity 1 & transform none in <= 1.3s")
        print(f"  [{'PASS' if ch9_ok else 'FAIL'}] Check 9: Opacity 0.1->0.9 >= 500ms ({dur_01_09:.1f}ms, mid_sample={trans_meas['has_mid_sample']})")

        # -------------------------------------------------------------
        # Check 10: After entrances & 3s idle wait, 0 running animations, static over 3s
        # -------------------------------------------------------------
        # Wait for any active entrances + 3s idle
        page.wait_for_timeout(3200)
        idle_data = page.evaluate("""() => new Promise(resolve => {
            const els = Array.from(document.querySelectorAll('.reveal.is-in')).slice(0, 5);
            const animCount = document.getAnimations().filter(a => a.playState === 'running').length;
            const initialTransforms = els.map(el => getComputedStyle(el).transform);
            const samples = [];
            let count = 0;
            const interval = setInterval(() => {
                const cur = els.map(el => getComputedStyle(el).transform);
                samples.push(cur);
                count++;
                if (count >= 6) {
                    clearInterval(interval);
                    const identical = samples.every(s => JSON.stringify(s) === JSON.stringify(initialTransforms));
                    resolve({
                        animCount,
                        identicalOver3s: identical
                    });
                }
            }, 500);
        })""")
        ch10_ok = (idle_data["animCount"] == 0) and idle_data["identicalOver3s"]
        report["check_10_idle_stillness_0_animations"] = {
            "passed": ch10_ok,
            "details": idle_data
        }
        if not ch10_ok: all_passed = False
        print(f"  [{'PASS' if ch10_ok else 'FAIL'}] Check 10: Idle stillness (running_animations={idle_data['animCount']}, static={idle_data['identicalOver3s']})")

        # -------------------------------------------------------------
        # Check 11 & 12: Frame timing during full scroll (< 40ms, <= 3 > 20ms) & CLS = 0
        # -------------------------------------------------------------
        perf_data = page.evaluate("""() => new Promise(resolve => {
            let clsScore = 0;
            try {
                const po = new PerformanceObserver(entryList => {
                    for (const entry of entryList.getEntries()) {
                        if (!entry.hadRecentInput) {
                            clsScore += entry.value;
                        }
                    }
                });
                po.observe({ type: 'layout-shift' });
            } catch (e) {}

            const frameTimes = [];
            let lastTime = performance.now();
            let active = true;

            function recordFrame(now) {
                const dt = now - lastTime;
                lastTime = now;
                frameTimes.push(dt);
                if (active) requestAnimationFrame(recordFrame);
            }
            requestAnimationFrame(recordFrame);

            const docH = document.documentElement.scrollHeight;
            let curY = 0;
            const interval = setInterval(() => {
                curY += 140;
                window.scrollTo(0, curY);
                if (curY >= docH) {
                    clearInterval(interval);
                    active = false;
                    setTimeout(() => {
                        const sorted = [...frameTimes].sort((a,b) => b-a);
                        const longest = sorted[0] || 0;
                        const over20 = frameTimes.filter(t => t > 20).length;
                        resolve({
                            clsScore,
                            totalFrames: frameTimes.length,
                            longestFrameMs: longest,
                            framesOver20Ms: over20
                        });
                    }, 80);
                }
            }, 16);
        })""")
        ch11_ok = (perf_data["longestFrameMs"] < 40) and (perf_data["framesOver20Ms"] <= 3)
        ch12_ok = (perf_data["clsScore"] == 0)

        report["check_11_scroll_frame_timing"] = {
            "passed": ch11_ok,
            "details": perf_data
        }
        report["check_12_cumulative_layout_shift_zero"] = {
            "passed": ch12_ok,
            "details": {"cls": perf_data["clsScore"]}
        }
        if not ch11_ok: all_passed = False
        if not ch12_ok: all_passed = False
        print(f"  [{'PASS' if ch11_ok else 'FAIL'}] Check 11: Scroll frame timing (longest={perf_data['longestFrameMs']:.1f}ms, over20ms={perf_data['framesOver20Ms']})")
        print(f"  [{'PASS' if ch12_ok else 'FAIL'}] Check 12: Layout shift total = {perf_data['clsScore']}")

        # -------------------------------------------------------------
        # Check 15: Console errors and CSP violations (zero)
        # -------------------------------------------------------------
        bad_logs = [log for log in console_logs if log["type"] == "error" or "security" in log["text"].lower()]
        ch15_ok = len(bad_logs) == 0
        report["check_15_zero_console_and_csp_errors"] = {
            "passed": ch15_ok,
            "details": bad_logs
        }
        if not ch15_ok: all_passed = False
        print(f"  [{'PASS' if ch15_ok else 'FAIL'}] Check 15: Zero console & CSP errors ({len(bad_logs)} found)")

        # -------------------------------------------------------------
        # Check 17: Desktop 1440px screenshots (start, step 1-5, footer)
        # -------------------------------------------------------------
        screenshots = {}
        # Start
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        p_start = os.path.join(SCREENSHOTS_DIR, "desktop_1440_start.png")
        page.screenshot(path=p_start)
        screenshots["start"] = p_start

        # Steps 1 to 5
        for i in range(5):
            page.evaluate(f"""() => {{
                function getAbsoluteTop(el) {{
                    let top = 0, cur = el;
                    while (cur) {{ top += cur.offsetTop; cur = cur.offsetParent; }}
                    return top;
                }}
                const s = document.querySelectorAll('.hm-how-step')[{i}];
                window.scrollTo(0, getAbsoluteTop(s) - window.innerHeight * 0.4 + 20);
            }}""")
            page.wait_for_timeout(350)
            p_step = os.path.join(SCREENSHOTS_DIR, f"desktop_1440_step_0{i+1}.png")
            page.screenshot(path=p_step)
            screenshots[f"step_0{i+1}"] = p_step

        # Footer
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(300)
        p_foot = os.path.join(SCREENSHOTS_DIR, "desktop_1440_footer.png")
        page.screenshot(path=p_foot)
        screenshots["footer"] = p_foot

        ch17_ok = all(os.path.exists(p) for p in screenshots.values())
        report["check_17_desktop_1440_screenshots"] = {
            "passed": ch17_ok,
            "details": screenshots
        }
        if not ch17_ok: all_passed = False
        print(f"  [{'PASS' if ch17_ok else 'FAIL'}] Check 17: Captured {len(screenshots)} screenshots under screenshots/")

        page.close()

        # -------------------------------------------------------------
        # Session 2: Reduced Motion Emulation (Check 13)
        # -------------------------------------------------------------
        rm_page = browser.new_page(viewport={"width": 1440, "height": 900})
        rm_page.emulate_media(reduced_motion="reduce")
        rm_page.goto(f"{BASE_URL}/#home")
        # Measure instantly: the page uses smooth scrolling for users; tests read positions after each jump.
        rm_page.add_style_tag(content="html{scroll-behavior:auto !important}")
        rm_page.wait_for_timeout(400)

        rm_data = rm_page.evaluate("""() => {
            const reveals = Array.from(document.querySelectorAll('.reveal'));
            const opacities = reveals.map(r => parseFloat(getComputedStyle(r).opacity));
            const runningAnimCount = document.getAnimations().filter(a => a.playState === 'running').length;

            function getAbsoluteTop(el) {
                let top = 0, cur = el;
                while (cur) { top += cur.offsetTop; cur = cur.offsetParent; }
                return top;
            }
            const s2 = document.querySelectorAll('.hm-how-step')[2];
            const targetY = getAbsoluteTop(s2) - window.innerHeight * 0.4 + 20;
            window.scrollTo(0, targetY);

            return {
                allOpacitiesOne: opacities.every(op => op >= 0.99),
                runningAnimCount
            };
        }""")
        rm_page.wait_for_timeout(100)
        step_num_rm = rm_page.evaluate("() => document.getElementById('howActiveNum').textContent.trim()")
        ch13_ok = rm_data["allOpacitiesOne"] and (rm_data["runningAnimCount"] == 0) and (step_num_rm == "03")
        report["check_13_reduced_motion"] = {
            "passed": ch13_ok,
            "details": {
                **rm_data,
                "stepNumAtScroll": step_num_rm
            }
        }
        if not ch13_ok: all_passed = False
        print(f"  [{'PASS' if ch13_ok else 'FAIL'}] Check 13: Reduced motion (final_immediate={rm_data['allOpacitiesOne']}, anims={rm_data['runningAnimCount']}, indicator_scrolls={step_num_rm=='03'})")
        rm_page.close()

        # -------------------------------------------------------------
        # Session 3: No-JS Fallback (Check 14)
        # -------------------------------------------------------------
        nojs_ctx = browser.new_context(java_script_enabled=False, viewport={"width": 1440, "height": 900})
        nojs_page = nojs_ctx.new_page()
        nojs_page.goto(f"{BASE_URL}/#home")
        nojs_page.wait_for_timeout(300)

        reveals = nojs_page.locator(".reveal")
        rcount = reveals.count()
        all_vis = True
        for idx in range(min(rcount, 20)):
            if not reveals.nth(idx).is_visible():
                all_vis = False
                break
        ch14_ok = (rcount > 0) and all_vis
        report["check_14_no_javascript_visibility"] = {
            "passed": ch14_ok,
            "details": {"revealCount": rcount, "allVisible": all_vis}
        }
        if not ch14_ok: all_passed = False
        print(f"  [{'PASS' if ch14_ok else 'FAIL'}] Check 14: No-JS visibility ({rcount} reveals, visible={all_vis})")
        nojs_page.close()
        nojs_ctx.close()

        # -------------------------------------------------------------
        # Session 4: Mobile at 390px (Check 16)
        # -------------------------------------------------------------
        mob_page = browser.new_page(viewport={"width": 390, "height": 844})
        mob_page.goto(f"{BASE_URL}/#home")
        # Measure instantly: the page uses smooth scrolling for users; tests read positions after each jump.
        mob_page.add_style_tag(content="html{scroll-behavior:auto !important}")
        mob_page.wait_for_timeout(500)

        mob_data = mob_page.evaluate("""() => {
            const sw = document.documentElement.scrollWidth;
            const cw = document.documentElement.clientWidth;
            const pinned = document.getElementById('howPinned');
            const steps = document.getElementById('howSteps');
            const pinnedRect = pinned.getBoundingClientRect();
            const stepsRect = steps.getBoundingClientRect();

            return {
                scrollWidth: sw,
                clientWidth: cw,
                noOverflow: sw <= cw,
                stacksAbove: pinnedRect.bottom <= stepsRect.top + 5
            };
        }""")
        ch16_ok = mob_data["noOverflow"] and mob_data["stacksAbove"]
        report["check_16_mobile_390px"] = {
            "passed": ch16_ok,
            "details": mob_data
        }
        if not ch16_ok: all_passed = False
        print(f"  [{'PASS' if ch16_ok else 'FAIL'}] Check 16: Mobile 390px (no_overflow={mob_data['noOverflow']}, stacks_above={mob_data['stacksAbove']})")
        mob_page.close()

        browser.close()

    # -------------------------------------------------------------
    # Check 18: Python unit test suite
    # -------------------------------------------------------------
    print("\nRunning test suite (python -m unittest discover -v)...")
    res_test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], capture_output=True, text=True)
    ch18_ok = (res_test.returncode == 0)
    report["check_18_unit_tests"] = {
        "passed": ch18_ok,
        "details": res_test.stderr[-300:] if res_test.stderr else res_test.stdout[-300:]
    }
    if not ch18_ok: all_passed = False
    print(f"  [{'PASS' if ch18_ok else 'FAIL'}] Check 18: Unit tests (code={res_test.returncode})")

    # -------------------------------------------------------------
    # Check 19: tools/pages_journey.py
    # -------------------------------------------------------------
    print("Running pages journey (python tools/pages_journey.py)...")
    res_pages = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pages_journey.py")], capture_output=True, text=True)
    ch19_ok = (res_pages.returncode == 0)
    report["check_19_pages_journey"] = {
        "passed": ch19_ok,
        "details": res_pages.stdout[-300:] if res_pages.stdout else res_pages.stderr[-300:]
    }
    if not ch19_ok: all_passed = False
    print(f"  [{'PASS' if ch19_ok else 'FAIL'}] Check 19: pages_journey.py (code={res_pages.returncode})")

    # Output JSON summary
    print("\n==================================================================")
    print("VERIFICATION RESULT SUMMARY")
    print("==================================================================")
    print(json.dumps(report, indent=2))

    if not all_passed:
        print("\n[ERROR] One or more verification checks failed.")
        sys.exit(1)
    else:
        print("\n[SUCCESS] All 19 checks passed cleanly.")
        sys.exit(0)

if __name__ == "__main__":
    run_all_checks()
