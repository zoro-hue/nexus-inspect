"""Browser journey for the Phase 5 investigation section (real Chromium, real analysis of a labelled fixture).

Usage: python tools/investigation_journey.py [BASE_URL] [FIXTURE_DIR]
Signs up, analyses the multi-file fixture, opens the analysis from Overview and checks the Investigation section on
desktop and at 390px: overall risk, methodology, evidence-class counts, facts, root-cause hypotheses, and the AI note
(the server has no key in this run, so no AI button should appear). Prints a JSON report; exits non-zero on failure.
"""
import io
import json
import os
import tempfile
import sys
import time
import zipfile

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
FIXTURE = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures", "corpus", "multi_file_correlation")
OUT = os.environ.get("SHOTS", os.path.join(tempfile.gettempdir(), "nx", "investigation"))
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})


def fixture_zip(root):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name == "expected.json":
                    continue  # ground-truth labels must not be part of the repository under analysis
                full = os.path.join(dirpath, name)
                zf.write(full, os.path.relpath(full, root))
    path = os.path.join(OUT, "fixture.zip")
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return path


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 860})
    errors = []
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    graph_requests = []
    page.on("request", lambda r: graph_requests.append(r.url) if "/graph?" in r.url else None)

    email = f"invest-{os.getpid()}-{int(time.time())}@example.com"
    page.goto(BASE + "/#signup")
    page.wait_for_selector("#signupForm:not([hidden])", timeout=8000)
    page.fill("#signupName", "Investigation Tester")
    page.fill("#signupEmail", email)
    page.fill("#signupPassword", "correct-horse-1")
    page.click("#signupForm button[type=submit]")
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)

    page.goto(BASE + "/#analyze")
    page.wait_for_selector("#zipFile", state="attached")
    page.set_input_files("#zipFile", fixture_zip(FIXTURE))
    page.locator("#startBtn:not([disabled])").wait_for(timeout=5000)
    page.click("#startBtn")
    page.locator("#island[data-state='completed']").wait_for(timeout=120000)
    check("fixture analysis completes", True)

    page.goto(BASE + "/#overview")
    page.wait_for_selector("[data-open-analysis]", timeout=8000)
    page.click("[data-open-analysis] >> nth=0")
    page.wait_for_selector("#drawer h3:has-text('Investigation')", timeout=8000)
    page.wait_for_timeout(900)

    drawer = page.locator("#drawer")
    text = drawer.inner_text()
    check("drawer shows the overall risk", "Overall risk:" in text, "Overall risk:" in text)
    check("drawer states the risk methodology", "highest finding risk score" in text)
    check("drawer shows evidence-class counts",
          "Confirmed" in text and "Potential" in text and "Informational" in text)
    check("drawer lists observed facts", "Findings touch" in text)
    check("drawer lists root-cause hypotheses", "root-cause hypotheses" in text.lower())
    groups = drawer.locator(".inv-group")
    stub_group = drawer.locator(".inv-group", has_text="Function body is a placeholder with no implementation repeats across 2 files")
    check("the repeated stub group names both files", stub_group.count() == 1 and "pkg/exports.py" in stub_group.inner_text()
          and "pkg/reports.py" in stub_group.inner_text(), stub_group.count())
    check("a broken-import group is present",
          drawer.locator(".inv-group", has_text="imports a module that is not in the repository").count() == 1)
    check("groups are labelled as hypotheses", drawer.locator(".inv-group", has_text="Hypothesis:").count() == groups.count())
    check("no AI button without a provider key", drawer.locator("[data-explain-group]").count() == 0)
    check("AI status note explains how to enable AI",
          "NEXUS_AI_API_KEY" in text, "NEXUS_AI_API_KEY" in text)
    page.screenshot(path=f"{OUT}/01_investigation_desktop.png")
    drawer.locator("h3:has-text('Investigation')").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/02_investigation_groups_desktop.png")

    # Relationship graph: rendered from the server's /graph data, every relationship shows its evidence.
    try:
        drawer.locator("[data-graph-body] svg.graph-svg").wait_for(timeout=8000)
    except Exception:
        print("DEBUG graph body:", drawer.locator("[data-graph-body]").inner_text()[:600], file=sys.stderr)
        print("DEBUG errors:", errors[:5], file=sys.stderr)
        raise
    graph_svg = drawer.locator("svg.graph-svg")
    check("graph renders a diagram", graph_svg.count() == 1 and drawer.locator("g.g-node").count() >= 2,
          drawer.locator("g.g-node").count())
    check("graph lists the same items for keyboard users", drawer.locator(".graph-item").count() >= 2)
    rels = drawer.locator(".graph-rel")
    no_evidence = sum(1 for i in range(rels.count()) if rels.nth(i).locator(".file-list li").count() == 0)
    check("every relationship in the detail panel cites evidence", rels.count() > 0 and no_evidence == 0,
          f"{rels.count()} relationships, {no_evidence} without evidence")
    drawer.locator(".graph-item").nth(0).click()
    check("selecting an item updates the detail panel", drawer.locator(".graph-list .is-current").count() == 1)
    # keyboard: focus a diagram node and activate with Enter
    drawer.locator("g.g-node").nth(0).focus()
    page.keyboard.press("Enter")
    check("keyboard Enter selects a diagram node", drawer.locator(".graph-list .is-current").count() == 1)
    before = len(graph_requests)
    drawer.locator("#gCat").select_option("security")
    page.wait_for_timeout(700)
    after = len(graph_requests)
    check("changing a filter requests the graph again", after > before, (before, after))
    drawer.locator("#gCat").select_option("")
    page.wait_for_timeout(500)
    drawer.locator("[data-graph-body]").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/04_graph_desktop.png")

    mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mp = mobile.new_page()
    mp.on("pageerror", lambda e: errors.append(f"mobile: {e}"))
    mp.goto(BASE + "/#signin")
    mp.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
    mp.fill("#signinEmail", email)
    mp.fill("#signinPassword", "correct-horse-1")
    mp.click("#signinForm button[type=submit]")
    mp.wait_for_selector("#dash:not([hidden])", timeout=10000)
    mp.goto(BASE + "/#overview")
    mp.wait_for_selector("[data-open-analysis]", timeout=8000)
    mp.click("[data-open-analysis] >> nth=0")
    mp.wait_for_selector("#drawer h3:has-text('Investigation')", timeout=8000)
    mp.wait_for_timeout(900)
    overflow = mp.evaluate("document.getElementById('drawer').scrollWidth - document.getElementById('drawer').clientWidth")
    check("390px drawer has no horizontal overflow with investigation", overflow <= 0, overflow)
    mp.locator("#drawer h3:has-text('Investigation')").scroll_into_view_if_needed()
    mp.screenshot(path=f"{OUT}/03_investigation_mobile.png")
    mp.locator("[data-graph-body]").scroll_into_view_if_needed()
    graph_overflow = mp.evaluate("document.getElementById('drawer').scrollWidth - document.getElementById('drawer').clientWidth")
    check("390px graph has no horizontal overflow", graph_overflow <= 0, graph_overflow)
    mp.screenshot(path=f"{OUT}/05_graph_mobile.png")
    mobile.close()

    check("no page errors during the investigation journey", not errors, errors[:3])
    browser.close()

report = {"checks": results, "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}
print(json.dumps(report, indent=2))
sys.exit(1 if report["failed"] else 0)
