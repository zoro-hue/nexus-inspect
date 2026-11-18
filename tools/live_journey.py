"""Live dashboard journey against a running NEXUS INSPECT server (real Chromium).

Usage: python tools/live_journey.py [BASE_URL] [FIXTURE_ZIP]
Two tabs share one account. Tab A uploads a repository and observes the analysis island move through its states
from server events. Tab B, which never reloads, must show the new analysis and findings. Also covers reconnecting
after the network drops, back/forward between views, reduced motion and a 390px mobile width.
Prints a JSON report and exits non-zero if any check fails.
"""
import json
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _wait import wait_fn
import os
import tempfile
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ZIP = sys.argv[2] if len(sys.argv) > 2 else os.path.join(tempfile.gettempdir(), "nx", "demo-repository.zip")
SHOTS = os.environ.get("SHOTS", os.path.join(tempfile.gettempdir(), "nx", "shots"))
os.makedirs(SHOTS, exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})


def live_status(page):
    return page.evaluate("window.NexusLive ? window.NexusLive.status : 'missing'")


def island_state(page):
    return page.evaluate("document.getElementById('island').dataset.state")


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 860})
    errors = []

    a = ctx.new_page()
    a.on("pageerror", lambda e: errors.append(f"A pageerror: {e}"))
    a.on("console", lambda m: errors.append(f"A console: {m.text}") if m.type == "error" else None)

    # 1. Sign up in tab A through the real form.
    email = f"live-{os.getpid()}-{int(time.time())}@example.com"
    a.goto(BASE + "/#signup")
    a.wait_for_selector("#signupForm:not([hidden])", timeout=8000)
    a.fill("#signupName", "Live Tester")
    a.fill("#signupEmail", email)
    a.fill("#signupPassword", "correct-horse-1")
    a.click("#signupForm button[type=submit]")
    a.wait_for_selector("#dash:not([hidden])", timeout=10000)
    wait_fn(a, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    check("tab A opens the event stream after sign-up", live_status(a) == "live", live_status(a))

    # 2. Tab B shares the session cookie and must not reload during the run.
    b = ctx.new_page()
    b.on("pageerror", lambda e: errors.append(f"B pageerror: {e}"))
    b.goto(BASE + "/#overview")
    b.wait_for_selector("#dash:not([hidden]) section.view.active[data-view=overview]", timeout=8000)
    wait_fn(b, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    before = b.inner_text("#ovAnalyses")
    check("tab B opens its own stream", live_status(b) == "live", live_status(b))

    # 3. Tab A starts an analysis and records every island state it passes through.
    a.evaluate("""() => { window.__states = []; const el = document.getElementById('island');
      new MutationObserver(() => window.__states.push(el.dataset.state))
        .observe(el, { attributes: true, attributeFilter: ['data-state'] }); }""")
    a.goto(BASE + "/#analyze")
    a.wait_for_selector("#zipFile", state="attached")
    a.set_input_files("#zipFile", ZIP)
    wait_fn(a, "!document.getElementById('startBtn').disabled", timeout=5000)
    a.click("#startBtn")
    wait_fn(a, "document.getElementById('island').dataset.state === 'completed'", timeout=120000)
    states = a.evaluate("window.__states")
    seen = set(states)
    check("island passes through queued or analyzing", bool(seen & {"queued", "analyzing"}), states[:12])
    check("island reports completion from the event", "completed" in seen, states[-6:])
    check("island shows the finding count", a.inner_text("#islandCount").strip().isdigit(),
          a.inner_text("#islandCount"))
    a.screenshot(path=f"{SHOTS}/live_island_completed.png")

    # 4. Tab B updates without reloading: analysis count and findings change.
    wait_fn(b, f"document.getElementById('ovAnalyses').textContent !== {json.dumps(before)}", timeout=15000)
    check("tab B overview updates from events without a reload", b.inner_text("#ovAnalyses") != before,
          f"{before} -> {b.inner_text('#ovAnalyses')}")
    api_total = a.evaluate("""async () => {
      const list = await (await fetch('/api/analyses', {credentials:'same-origin'})).json();
      const done = list.items.find((x) => x.status === 'completed');
      const f = await (await fetch('/api/analyses/' + done.id + '/findings', {credentials:'same-origin'})).json();
      return f.summary.total; }""")
    wait_fn(b, f"document.getElementById('ovFindings').textContent === '{api_total}'", timeout=15000)
    check("tab B findings count matches the persisted API total", True, api_total)

    # 5. Reconnect: tab A goes offline, a change is made through the API meanwhile, and tab A must catch up on reconnect.
    a.goto(BASE + "/#findings")
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=5000)
    wait_fn(a, "window.NexusLive.status === 'live'", timeout=8000)
    cookies = ctx.cookies(BASE)
    header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    api_req = p.request.new_context(base_url=BASE, extra_http_headers={"Cookie": header})
    analyses = api_req.get("/api/analyses").json()["items"]
    done = next(x for x in analyses if x["status"] == "completed")
    findings = api_req.get(f"/api/analyses/{done['id']}/findings").json()["items"]
    fid = findings[0]["id"]

    ctx.set_offline(True)
    wait_fn(a, "window.NexusLive.status !== 'live'", timeout=10000)
    check("tab A notices the dropped stream", live_status(a) != "live", live_status(a))
    patched = api_req.patch(f"/api/analyses/{done['id']}/findings/{fid}", data=json.dumps({"status": "confirmed"}),
                            headers={"Content-Type": "application/json"})
    check("finding changed through the API while tab A was offline", patched.status == 200, patched.status)
    refetched = []
    a.on("request", lambda req: refetched.append(req.url) if "/findings" in req.url else None)
    ctx.set_offline(False)
    wait_fn(a, "window.NexusLive.status === 'live'", timeout=30000)
    check("tab A reconnects after the network returns", live_status(a) == "live", live_status(a))
    a.wait_for_timeout(1500)
    check("tab A refetches findings after reconnecting", any("/findings" in u for u in refetched), refetched[:2])
    api_req.dispose()

    # 6. Back and forward between views keep their state.
    a.goto(BASE + "/#overview")
    a.click(".capsule-link[data-view=findings]")
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=5000)
    a.go_back()
    a.wait_for_selector("section.view.active[data-view=overview]", timeout=5000)
    check("back returns to overview", True)
    a.go_forward()
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=5000)
    check("forward returns to findings", True)
    a.reload()
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=8000)
    check("refresh on a nested route keeps the route", True)

    # 7. Mobile width: the island and the page never overflow.
    m = browser.new_context(viewport={"width": 390, "height": 844})
    mp = m.new_page()
    mp.on("pageerror", lambda e: errors.append(f"M pageerror: {e}"))
    mp.goto(BASE + "/#signin")
    mp.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
    mp.fill("#signinEmail", email)
    mp.fill("#signinPassword", "correct-horse-1")
    mp.click("#signinForm button[type=submit]")
    mp.wait_for_selector("#dash:not([hidden])", timeout=10000)
    mp.wait_for_selector("#island", state="visible", timeout=5000)
    overflow = mp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    island_w = mp.evaluate("document.getElementById('island').getBoundingClientRect().width")
    check("mobile page does not overflow horizontally", overflow <= 0, overflow)
    check("mobile island fits the viewport", island_w <= 390, island_w)
    mp.screenshot(path=f"{SHOTS}/live_mobile.png")
    m.close()

    # 8. Reduced motion: no transition is started and the stream still works.
    r = browser.new_context(viewport={"width": 1100, "height": 800}, reduced_motion="reduce")
    rp = r.new_page()
    rp.on("pageerror", lambda e: errors.append(f"R pageerror: {e}"))
    rp.goto(BASE + "/#signin")
    rp.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
    rp.fill("#signinEmail", email)
    rp.fill("#signinPassword", "correct-horse-1")
    rp.click("#signinForm button[type=submit]")
    rp.wait_for_selector("#dash:not([hidden])", timeout=10000)
    wait_fn(rp, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    started = rp.evaluate("""() => { const orig = document.startViewTransition; let called = false;
      document.startViewTransition = (...args) => { called = true; return orig ? orig.apply(document, args) : undefined; };
      document.querySelector('.capsule-link[data-view=projects]').click(); return called === false; }""")
    rp.wait_for_selector("section.view.active[data-view=projects]", timeout=5000)
    check("reduced motion skips View Transitions", started)
    check("reduced motion keeps the stream live", live_status(rp) == "live", live_status(rp))
    r.close()

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    check("no page errors or console errors during the journey", not real_errors, real_errors[:3])

    browser.close()

passed = sum(1 for r_ in results if r_["ok"])
print(json.dumps({"passed": passed, "total": len(results), "checks": results}, indent=2))
sys.exit(0 if passed == len(results) else 1)
