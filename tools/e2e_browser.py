"""End-to-end browser check against a running NEXUS INSPECT server.

Usage: python tools/e2e_browser.py [BASE_URL] [FIXTURE_ZIP] [SECRET_VALUE]
Drives the real site with headless Chromium: homepage, navigation, upload analysis, findings, detail, triage, reports.
Prints a JSON report of every check; exits non-zero if any check fails.
"""
import json
import os
import tempfile
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ZIP = sys.argv[2] if len(sys.argv) > 2 else os.path.join(tempfile.gettempdir(), "nx", "demo-repository.zip")
SECRET = sys.argv[3] if len(sys.argv) > 3 else ""
SHOTS = os.environ.get("SHOTS", os.path.join(tempfile.gettempdir(), "nx", "shots"))
os.makedirs(SHOTS, exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1366, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

    # 0. Sign up through the real form. The browser context then carries the session cookie for every
    #    dashboard request and for page.request exports, exactly as a user's browser would.
    email = f"e2e-{os.getpid()}-{int(time.time())}@example.com"
    page.goto(BASE + "/#signup")
    page.wait_for_selector("#signupForm:not([hidden])", timeout=5000)
    page.fill("#signupName", "E2E Tester")
    page.fill("#signupEmail", email)
    page.fill("#signupPassword", "correct-horse-1")
    page.click("#signupForm button[type=submit]")
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    check("signs up through the real form", True)

    # 0b. Deep link: a dashboard hash opened directly on a fresh load shows the dashboard (session cookie present).
    page.goto(BASE + "/#analyze")
    page.wait_for_selector("#dash:not([hidden]) section.view.active[data-view=analyze]", timeout=5000)
    check("direct dashboard deep link opens the dashboard", True)
    page.evaluate("localStorage.clear()")

    # 1. Homepage: hero, showcase labelled as illustrative, no connected-circle graph.
    page.goto(BASE + "/#home")
    page.wait_for_selector(".hm-hero")
    check("homepage renders hero", page.locator(".hm-hero h1").count() == 1)
    check("homepage has no repository graph", page.locator(".hm-node, .hm-edge, .hm-svg").count() == 0)
    check("showcase is labelled illustrative", page.locator(".hm-badge", has_text="Illustrative sample").count() >= 1)
    page.locator("#showStage").scroll_into_view_if_needed()
    page.wait_for_timeout(6500)
    check("showcase reveals findings", page.locator("#showList .hm-item").count() >= 3,
          page.locator("#showList .hm-item").count())
    page.screenshot(path=f"{SHOTS}/home-showcase.png", full_page=False)

    # 1b. Navbar never clips: after hash navigation the fixed header sits at the top of the viewport.
    page.goto(BASE + "/#home"); page.wait_for_timeout(1200)
    header_y = page.locator("#gnav").bounding_box()["y"]
    check("navbar stays at the top of the viewport", abs(header_y) < 1, header_y)

    # 2. Mega menu: open on hover, stay open while the pointer moves into it, close on leave.
    page.goto(BASE + "/#home")
    page.wait_for_selector("[data-menu=product]")
    page.hover("[data-menu=product]")
    page.wait_for_timeout(400)
    mega_open = page.locator("#mega").is_visible()
    check("mega menu opens on hover", mega_open)
    box = page.locator("[data-menu=product]").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] + 6, steps=4)
    page.wait_for_timeout(150)
    check("mega menu stays open when pointer crosses to it", page.locator("#mega").is_visible())
    page.screenshot(path=f"{SHOTS}/mega-menu.png")
    page.hover("[data-menu=capabilities]"); page.wait_for_timeout(400)
    check("hovering another menu switches the panel", page.get_attribute("[data-menu=capabilities]", "aria-expanded") == "true")
    page.mouse.move(683, 414, steps=6)  # below the header, outside the menu
    page.wait_for_timeout(600)
    check("mega menu closes after pointer leaves", not page.evaluate("document.getElementById('mega').classList.contains('open')"))

    # 3. Analyze: upload the repository fixture, watch the real analysis finish.
    page.goto(BASE + "/#analyze")
    page.wait_for_selector("#zipFile", state="attached")
    page.set_input_files("#zipFile", ZIP)
    page.wait_for_timeout(200)
    page.click("#startBtn")
    try:
        page.wait_for_selector("#summary:not([hidden])", timeout=30000)
        check("upload analysis completes", True)
    except Exception as exc:  # noqa: BLE001
        check("upload analysis completes", False, page.locator("#runStatus").inner_text() + " / " + str(exc)[:80])
    summary_total = page.locator("#sumTotal").inner_text()
    check("summary shows findings total", summary_total.isdigit() and int(summary_total) > 0, summary_total)
    page.screenshot(path=f"{SHOTS}/analyze-complete.png")

    # 4. Findings: list, filter, open detail with evidence excerpt.
    page.click("#viewFindingsBtn")
    page.wait_for_selector(".finding")
    total_items = page.locator(".finding").count()
    check("findings list is populated from the API", total_items > 0, total_items)
    # The sample fixture has no critical findings, so the High filter is the one that can narrow it.
    page.click("[data-sev-filter=high]")
    page.wait_for_timeout(400)
    high_items = page.locator(".finding").count()
    check("severity filter narrows the list", 0 < high_items <= total_items, f"{high_items}/{total_items}")
    page.click("[data-sev-filter=all]")
    page.wait_for_timeout(400)
    page.locator(".finding").first.click()
    page.wait_for_selector("#drawer.open .excerpt")
    check("finding detail shows evidence excerpt", page.locator("#drawer .excerpt .ln.hl").count() >= 1)
    body_text = page.locator("#drawer").inner_text()
    check("finding detail shows remediation and verification", "recommended fix" in body_text.lower() and "how to verify" in body_text.lower())
    if SECRET:
        check("secret value never rendered in the UI", SECRET not in page.content(), "")
    page.screenshot(path=f"{SHOTS}/finding-detail.png")

    # 5. Triage: suppression requires a reason; a reason lets it through and is recorded in history.
    page.select_option("#newStatus", "suppressed")
    page.click("#triageForm button[type=submit]")
    page.wait_for_timeout(300)
    err = page.locator("#triageErr").inner_text()
    check("suppression without a reason is refused", "reason" in err.lower(), err)
    page.fill("#reason", "Accepted risk for this demo fixture")
    page.click("#triageForm button[type=submit]")
    page.wait_for_timeout(700)
    check("suppression with a reason is recorded",
          "Suppressed" in page.locator("#drawer .detail-head").inner_text()
          and "Accepted risk" in page.locator("#drawer .history").inner_text())
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Escape closes the drawer", not page.locator("#drawer").evaluate("el => el.classList.contains('open')"))

    # 6. Projects and history.
    page.goto(BASE + "/#projects")
    page.wait_for_selector(".project")
    check("project list shows the imported repository", page.locator(".project").count() >= 1)
    page.locator(".project button").first.click()
    page.wait_for_selector("#drawer.open .history-row")
    check("project history lists analyses", page.locator("#drawer .history-row").count() >= 1)
    page.keyboard.press("Escape")

    # 7. Reports: all three exports point at the API and return the right content types.
    page.goto(BASE + "/#reports")
    page.wait_for_selector("#repSarif")
    page.wait_for_timeout(500)
    report_ok = True
    for sel, fmt, marker in [("#repJson", "json", '"findings"'), ("#repMd", "markdown", "# NEXUS INSPECT report"),
                             ("#repSarif", "sarif", '"version": "2.1.0"')]:
        href = page.get_attribute(sel, "data-href")
        resp = page.request.get(BASE + href)
        body = resp.text()
        if resp.status != 200 or marker not in body:
            report_ok = False
            check(f"export {fmt} downloads", False, f"{resp.status} {href}")
    check("JSON, Markdown and SARIF exports download", report_ok)
    page.click("#togglePreview")
    page.wait_for_timeout(400)
    check("Markdown preview loads from the API", "NEXUS INSPECT report" in page.locator("#mdPreview").inner_text())
    page.screenshot(path=f"{SHOTS}/reports.png")

    # 8. Overview reflects stored results.
    page.goto(BASE + "/#overview")
    page.wait_for_selector("#ovFindings")
    page.wait_for_timeout(700)
    check("overview shows stored findings", page.locator("#ovFindings").inner_text().isdigit()
          and int(page.locator("#ovFindings").inner_text()) > 0)
    check("connection indicator reports online", "online" in page.locator("#connStatus").inner_text().lower())
    fonts = page.evaluate("""async () => { await document.fonts.ready; return [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family); }""")
    check("Geist Pixel, Geist and Geist Mono load from the app", all(any(f.startswith(n) for f in fonts) for n in ("Geist Pixel", "Geist", "Geist Mono")), fonts)
    title_font = page.evaluate("getComputedStyle(document.querySelector('[data-view=overview] .page-title')).fontFamily")
    check("dashboard titles use Geist Pixel", "Geist Pixel" in title_font, title_font[:40])
    anim = page.evaluate("getComputedStyle(document.querySelector('.view.active')).animationName")
    check("dashboard views animate in", anim not in ("none", ""), anim)
    page.screenshot(path=f"{SHOTS}/overview.png")

    # 8b. AI: explanations are optional and report their own availability.
    page.goto(BASE + "/#findings"); page.wait_for_selector(".finding"); page.locator(".finding").first.click()
    page.wait_for_selector("#drawer.open #aiNote")
    page.wait_for_timeout(500)
    ai_note = page.locator("#aiNote").inner_text()
    check("AI panel reports availability honestly", "NEXUS_AI_API_KEY" in ai_note or "Model:" in ai_note, ai_note[:80])
    page.keyboard.press("Escape")
    # Transitions: the mega menu and the drawer animate rather than snap.
    page.goto(BASE + "/#home"); page.wait_for_selector("[data-menu=product]")
    mega_transition = page.evaluate("getComputedStyle(document.getElementById('mega')).transitionDuration")
    check("mega menu has a visible transition", mega_transition not in ("0s", ""), mega_transition)
    drawer_transition = page.evaluate("getComputedStyle(document.getElementById('drawer')).transitionDuration")
    check("drawer has a visible transition", drawer_transition not in ("0s", ""), drawer_transition)

    # 8c. Mobile navbar: brand and actions do not overlap; the Menu toggle opens the sheet.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + "/#home"); page.wait_for_timeout(900)
    brand = page.locator(".gnav .brand").bounding_box()
    actions = page.locator(".gnav-actions").bounding_box()
    check("mobile navbar: brand and actions do not overlap", brand["x"] + brand["width"] <= actions["x"] + 1,
          f"{brand['x'] + brand['width']:.0f} vs {actions['x']:.0f}")
    page.click(".menu-toggle")
    page.wait_for_timeout(450)
    check("mobile menu toggle opens the sheet", float(page.evaluate("getComputedStyle(document.getElementById('mobileSheet')).opacity")) > 0.9)
    page.click(".menu-toggle")
    page.wait_for_timeout(300)
    page.set_viewport_size({"width": 1366, "height": 860})

    # 8d. Every in-page link resolves to a section or a dashboard view.
    page.goto(BASE + "/#home"); page.wait_for_timeout(500)
    hrefs = page.evaluate("[...new Set([...document.querySelectorAll('a[href^=\"#\"]')].map(a => a.getAttribute('href')))]")
    dead = []
    for h in hrefs:
        target = h[1:]
        if target.split("/")[0] in ("", "home", "signin", "signup", "docs", "api"):  # routes handled by the router, not elements
            continue
        exists = page.evaluate("(id) => !!document.getElementById(id) || !!document.querySelector(`[data-view=\"${id}\"]`)", target)
        if not exists:
            dead.append(h)
    check("no dead in-page links", not dead, dead)

    # 9. Layout: no horizontal overflow at desktop and mobile widths.
    for width in (1366, 390):
        page.set_viewport_size({"width": width, "height": 860})
        for route in ("#home", "#overview", "#findings"):
            page.goto(BASE + "/" + route)
            page.wait_for_timeout(900)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check(f"no horizontal overflow {route} at {width}px", overflow <= 1, overflow)

    check("no page or console errors", not errors, "; ".join(errors)[:300])
    browser.close()

failed = [r for r in results if not r["ok"]]
print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "checks": results}, indent=2))
sys.exit(1 if failed else 0)
