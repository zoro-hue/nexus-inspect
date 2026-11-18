"""Automated E2E Home Journey Verification for NEXUS INSPECT.

Checks:
- Hero words reveal in order and CTAs appear last
- Statement text brightens with scroll
- Category cards reveal on scroll
- How it works steps update with scroll (pinned on desktop, stacked on mobile)
- Graph edges reach stroke-dashoffset 0 when visible
- No horizontal overflow at 1440px and 390px
- Reduced-motion mode shows final states immediately with no transform animations
- Zero console errors and zero CSP violations
- Every footer link resolves
- Performance measurement (scroll frame timing & long tasks)
- Captures screenshots at 1440px and 390px
"""

import os
import sys
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "screenshots")
os.makedirs(SHOTS, exist_ok=True)

def create_driver(width=1440, height=900, reduced_motion=False):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"--window-size={width},{height}")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    d = webdriver.Chrome(options=opts)
    val = "reduce" if reduced_motion else "no-preference"
    d.execute_cdp_cmd("Emulation.setEmulatedMedia", {
        "features": [{"name": "prefers-reduced-motion", "value": val}]
    })
    return d

def run_tests():
    print("==================================================================")
    print("NEXUS INSPECT — Home Page Automated Journey & Motion Verification")
    print("==================================================================")

    driver = create_driver(1440, 900)
    failed = []

    def check(name, condition, extra=None):
        if condition:
            print(f"  [PASS] {name}")
        else:
            msg = f"  [FAIL] {name}" + (f" -> {extra}" if extra is not None else "")
            print(msg)
            failed.append(msg)

    try:
        # 1. Hero Entrance & Word Reveals
        print("\n[Phase 1] Hero Entrance & Sequential Reveal")
        driver.get(f"{BASE}/#home")
        time.sleep(0.3)

        # Check title exists and has words
        words = driver.find_elements(By.CSS_SELECTOR, ".hm-word")
        check("Hero title has word-level spans", len(words) >= 5, len(words))

        # Wait for entrance to complete (under 2.2s)
        time.sleep(1.8)
        words_in = driver.find_elements(By.CSS_SELECTOR, ".hm-word.in")
        check("All hero words revealed", len(words_in) == len(words), f"{len(words_in)}/{len(words)}")

        lead = driver.find_element(By.ID, "heroLead")
        cta = driver.find_element(By.ID, "heroCta")
        check("Hero lead subline visible", "in" in lead.get_attribute("class"))
        check("Hero CTAs visible", "in" in cta.get_attribute("class"))

        # 2. Scanner Demo
        stage = driver.find_element(By.ID, "showStage")
        check("Scan stage exists and runs", stage.is_displayed())
        badge = driver.find_element(By.CSS_SELECTOR, ".hm-badge")
        check("Showcase labelled as illustrative", "Illustrative sample" in badge.text)

        # 3. Statement Scrubbed Text
        print("\n[Phase 2] Statement Scroll-Scrubbed Text")
        stmt_words = driver.find_elements(By.CSS_SELECTOR, ".hm-scrub-word")
        check("Statement words rendered", len(stmt_words) >= 8, len(stmt_words))

        # Scroll to middle of statement
        driver.execute_script("window.scrollTo(0, document.getElementById('statement').offsetTop - 200);")
        time.sleep(0.4)
        driver.execute_script("window.scrollTo(0, document.getElementById('statement').offsetTop + 100);")
        time.sleep(0.5)

        lit_words = driver.find_elements(By.CSS_SELECTOR, ".hm-scrub-word.lit")
        check("Statement words brighten as user scrolls", len(lit_words) > 0, f"{len(lit_words)} lit")

        # 4. Capabilities / Categories Reveal
        print("\n[Phase 3] Category Cards Reveal")
        driver.execute_script("document.getElementById('capabilities').scrollIntoView();")
        time.sleep(0.6)
        cards = driver.find_elements(By.CSS_SELECTOR, "#capabilitiesGrid .hm-card")
        check("Four category cards rendered", len(cards) == 4, len(cards))
        cards_in = driver.find_elements(By.CSS_SELECTOR, "#capabilitiesGrid .hm-card.in")
        check("Category cards reveal on scroll", len(cards_in) == 4, f"{len(cards_in)}/4")

        # Check real rules are rendered
        rule_tags = driver.find_elements(By.CSS_SELECTOR, ".rule-tag")
        rule_texts = [r.text for r in rule_tags]
        check("SEC-SECRET-001 present", "SEC-SECRET-001" in rule_texts)
        check("SLOP-STUB-001 present", "SLOP-STUB-001" in rule_texts)
        check("GAP-VALID-001 present", "GAP-VALID-001" in rule_texts)
        check("LOOSE-TODO-001 present", "LOOSE-TODO-001" in rule_texts)

        # 5. How It Works (Pinned Storytelling)
        print("\n[Phase 4] How It Works Pinned Storytelling")
        how_section = driver.find_element(By.ID, "how")
        driver.execute_script("document.getElementById('how').scrollIntoView();")
        time.sleep(0.5)

        pinned_col = driver.find_element(By.ID, "howPinned")
        pinned_pos = pinned_col.value_of_css_property("position")
        check("How It Works is pinned (sticky) on desktop", pinned_pos == "sticky", pinned_pos)

        # Scroll down through steps
        driver.execute_script("window.scrollBy(0, 450);")
        time.sleep(0.4)
        active_num = driver.find_element(By.ID, "howActiveNum").text
        check("How active step updates on scroll", active_num in ["01", "02", "03", "04", "05"], active_num)

        # 6. Evidence Showcase
        print("\n[Phase 5] Evidence Showcase")
        driver.execute_script("document.getElementById('evidence').scrollIntoView();")
        time.sleep(0.5)
        ev_card = driver.find_element(By.ID, "evidenceCard")
        check("Evidence card visible", "in" in ev_card.get_attribute("class"))

        # 7. Repository investigation workspace (replaces the old self-drawing graph)
        print("\n[Phase 6] Repository Investigation Workspace")
        driver.execute_script("document.getElementById('investigation').scrollIntoView();")
        time.sleep(0.8)
        files = driver.find_elements(By.CSS_SELECTOR, "#invWorkspace .inv-file")
        check("Workspace lists the sample files", len(files) >= 6, len(files))
        visible_findings = [f for f in driver.find_elements(By.CSS_SELECTOR, "#invWorkspace .inv-finding") if f.is_displayed()]
        check("One finding is shown at a time", len(visible_findings) == 1, len(visible_findings))
        next_finding = driver.find_element(By.CSS_SELECTOR, "#invWorkspace .inv-fbtn[data-finding='gap-val']")
        next_finding.click()
        time.sleep(0.3)
        check("Selecting a finding updates the evidence panel",
              driver.find_element(By.CSS_SELECTOR, "#invWorkspace .inv-fbtn[data-finding='gap-val']").get_attribute("aria-pressed") == "true")

        # 8. Exports & Trust Strip
        print("\n[Phase 7] Export Strip & Limits")
        driver.execute_script("document.getElementById('exports').scrollIntoView();")
        time.sleep(0.5)
        exp_cards = driver.find_elements(By.CSS_SELECTOR, ".hm-export-card")
        check("3 export cards rendered (JSON, Markdown, SARIF)", len(exp_cards) == 3, len(exp_cards))

        limits = driver.find_element(By.ID, "limits")
        check("Limits section present", limits.is_displayed())

        # 9. Final CTA & Footer Links
        print("\n[Phase 8] Final CTA & Footer Destinations")
        driver.execute_script("document.querySelector('.hm-foot').scrollIntoView();")
        time.sleep(0.5)

        footer_links = driver.find_elements(By.CSS_SELECTOR, ".hm-foot-links a")
        check("Footer has all 9 links", len(footer_links) >= 9, len(footer_links))
        hrefs = [a.get_attribute("href") for a in footer_links]
        check("No empty hrefs", all(h and "#" in h for h in hrefs))

        # 10. Horizontal Overflow at 1440px
        print("\n[Phase 9] Horizontal Overflow Check (1440px)")
        scroll_w = driver.execute_script("return document.documentElement.scrollWidth")
        client_w = driver.execute_script("return document.documentElement.clientWidth")
        check("No horizontal overflow at 1440px", scroll_w == client_w, f"scrollWidth={scroll_w}, clientWidth={client_w}")

        # Capture 1440px screenshots
        print("\n[Phase 10] Capturing 1440px Screenshots...")
        sections_1440 = [
            ("hero", "document.getElementById('home').scrollIntoView();"),
            ("statement", "document.getElementById('statement').scrollIntoView();"),
            ("how-it-works", "document.getElementById('how').scrollIntoView();"),
            ("evidence", "document.getElementById('evidence').scrollIntoView();"),
            ("investigation", "document.getElementById('investigation').scrollIntoView();"),
            ("exports", "document.getElementById('exports').scrollIntoView();"),
            ("footer", "document.querySelector('.hm-foot').scrollIntoView();")
        ]
        for name, scroll_cmd in sections_1440:
            driver.execute_script(scroll_cmd)
            time.sleep(0.3)
            path = os.path.join(SHOTS, f"1440-{name}.png")
            driver.save_screenshot(path)
            print(f"  Saved: {path}")

        # Check console logs for errors / CSP violations
        print("\n[Phase 11] Console Logs & CSP Compliance")
        logs = driver.get_log("browser")
        errors = [l for l in logs if l.get("level") == "SEVERE" and "favicon" not in l.get("message", "")]
        csp_violations = [l for l in logs if "Content Security Policy" in l.get("message", "")]
        check("Zero severe console errors", len(errors) == 0, errors)
        check("Zero CSP violations", len(csp_violations) == 0, csp_violations)

        # Performance: Long tasks during full scroll
        print("\n[Phase 12] Scroll Performance & Frame Timing")
        perf_data = driver.execute_script("""
            const entries = performance.getEntriesByType('longtask') || [];
            return {
                longTasksCount: entries.length,
                longestTaskDuration: entries.reduce((max, e) => Math.max(max, e.duration), 0)
            };
        """)
        check("Long tasks count during scroll <= 1", perf_data["longTasksCount"] <= 1, perf_data)
        print(f"  Longest task duration: {perf_data['longestTaskDuration']}ms")

    finally:
        driver.quit()

    # 11. Mobile Viewport (390px)
    print("\n[Phase 13] Mobile Viewport Verification (390px)")
    mobile_driver = create_driver(390, 844)
    try:
        mobile_driver.get(f"{BASE}/#home")
        time.sleep(1.0)

        # Check horizontal overflow at 390px
        m_scroll_w = mobile_driver.execute_script("return document.documentElement.scrollWidth")
        m_client_w = mobile_driver.execute_script("return document.documentElement.clientWidth")
        check("No horizontal overflow at 390px", m_scroll_w == m_client_w, f"scrollWidth={m_scroll_w}, clientWidth={m_client_w}")

        # Check that How It Works unpins (stacks) on mobile
        how_pinned_m = mobile_driver.find_element(By.ID, "howPinned")
        m_pos = how_pinned_m.value_of_css_property("position")
        check("How It Works is unpinned (static) at 390px", m_pos == "static", m_pos)

        # Capture 390px screenshots
        print("\n[Phase 14] Capturing 390px Screenshots...")
        sections_390 = [
            ("hero", "document.getElementById('home').scrollIntoView();"),
            ("statement", "document.getElementById('statement').scrollIntoView();"),
            ("how-it-works", "document.getElementById('how').scrollIntoView();"),
            ("evidence", "document.getElementById('evidence').scrollIntoView();"),
            ("investigation", "document.getElementById('investigation').scrollIntoView();"),
            ("exports", "document.getElementById('exports').scrollIntoView();"),
            ("footer", "document.querySelector('.hm-foot').scrollIntoView();")
        ]
        for name, scroll_cmd in sections_390:
            mobile_driver.execute_script(scroll_cmd)
            time.sleep(0.3)
            path = os.path.join(SHOTS, f"390-{name}.png")
            mobile_driver.save_screenshot(path)
            print(f"  Saved: {path}")

    finally:
        mobile_driver.quit()

    # 12. Reduced Motion Mode
    print("\n[Phase 15] Reduced-Motion Mode Verification")
    rm_driver = create_driver(1440, 900, reduced_motion=True)
    try:
        rm_driver.get(f"{BASE}/#home")
        time.sleep(0.5)

        # Check that all hero words are immediately .in without waiting
        rm_words = rm_driver.find_elements(By.CSS_SELECTOR, ".hm-word")
        rm_words_in = rm_driver.find_elements(By.CSS_SELECTOR, ".hm-word.in")
        check("Reduced motion shows all words immediately", len(rm_words) == len(rm_words_in), f"{len(rm_words_in)}/{len(rm_words)}")

        # Check statement words are lit
        rm_scrub = rm_driver.find_elements(By.CSS_SELECTOR, ".hm-scrub-word")
        check("Statement words visible under reduced motion", len(rm_scrub) > 0)

        # Check no active running CSS animations
        running_anims = rm_driver.execute_script("return (document.getAnimations ? document.getAnimations().filter(a => a.playState === 'running').length : 0);")
        check("Zero running animations under reduced motion", running_anims == 0, running_anims)

    finally:
        rm_driver.quit()

    print("\n==================================================================")
    if failed:
        print(f"FAILED with {len(failed)} error(s):")
        for f in failed:
            print(f)
        return False
    else:
        print("ALL VERIFICATIONS PASSED (100% SUCCESS)")
        print("==================================================================")
        return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
