"""Motion and interaction journey against a running NEXUS INSPECT server (real Chromium).

Usage: python tools/motion_journey.py [BASE_URL] [FIXTURE_ZIP]
Records a video of the whole journey and saves screenshots of transitions by pausing running animations at fixed
points. The checks cover behaviour (ghost panels, focus return, live row identity, stagger, reduced motion, mobile
width). Screenshots and video must still be inspected by a person or by the tester; a passing check is not a visual
verdict. Prints a JSON report and exits non-zero if a check fails.
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
OUT = os.environ.get("MOTION_OUT", os.path.join(tempfile.gettempdir(), "nx", "motion"))
os.makedirs(OUT, exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})


def ghosts(page):
    return page.evaluate("document.querySelectorAll('.fx-ghost').length")


def freeze_ghosts_at(page, ms):
    """Pause every running ghost animation at `ms` so a screenshot shows the mid-transition frame."""
    page.evaluate("""(ms) => document.getAnimations().forEach((a) => {
        if (a.effect && a.effect.target && a.effect.target.classList && a.effect.target.classList.contains('fx-ghost')) {
          a.pause(); a.currentTime = ms; } })""", ms)


def click_and_freeze(page, selector, ms):
    """Activate an element with a real DOM click and pause every running animation at `ms` in the same step, so
    the frame captured afterwards is deterministic and shows the transition mid-flight."""
    page.evaluate("""([sel, ms]) => { const el = document.querySelector(sel); el.click();
        document.getAnimations().forEach((a) => { a.pause(); a.currentTime = ms; }); }""", [selector, ms])


def close_and_freeze(page, ms):
    page.evaluate("""(ms) => { document.getElementById('drawerClose').click();
        document.getAnimations().forEach((a) => { a.pause(); a.currentTime = ms; }); }""", ms)


def play_all(page):
    page.evaluate("() => document.getAnimations().forEach((a) => a.play())")


def resume_ghosts(page):
    page.evaluate("""() => document.getAnimations().forEach((a) => {
        if (a.effect && a.effect.target && a.effect.target.classList && a.effect.target.classList.contains('fx-ghost')) a.play(); })""")


def sign_up(ctx, email, page_errors):
    page = ctx.new_page()
    page.on("pageerror", lambda e: page_errors.append(str(e) + " @ " + (e.stack or "").replace("\n", " | ")[:600]))
    page.goto(BASE + "/#signup")
    page.wait_for_selector("#signupForm:not([hidden])", timeout=8000)
    page.fill("#signupName", "Motion Tester")
    page.fill("#signupEmail", email)
    page.fill("#signupPassword", "correct-horse-1")
    page.click("#signupForm button[type=submit]")
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    return page


def sign_in(ctx, email, page_errors):
    page = ctx.new_page()
    page.on("pageerror", lambda e: page_errors.append(str(e) + " @ " + (e.stack or "").replace("\n", " | ")[:600]))
    page.goto(BASE + "/#signin")
    page.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
    page.fill("#signinEmail", email)
    page.fill("#signinPassword", "correct-horse-1")
    page.click("#signinForm button[type=submit]")
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    return page


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    errors = []
    email = f"motion-{os.getpid()}-{int(time.time())}@example.com"

    # ---------- Desktop: record the whole journey ----------
    video_dir = os.path.join(OUT, "video")
    ctx = browser.new_context(viewport={"width": 1366, "height": 860}, record_video_dir=video_dir,
                              record_video_size={"width": 1366, "height": 860})
    page = sign_up(ctx, email, errors)
    wait_fn(page, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)

    # 1. Upload a real repository and wait for the capsule to complete.
    page.goto(BASE + "/#analyze")
    page.wait_for_selector("#zipFile", state="attached")
    page.set_input_files("#zipFile", ZIP)
    wait_fn(page, "!document.getElementById('startBtn').disabled", timeout=5000)
    page.click("#startBtn")
    page.evaluate("""() => { window.__islandSeen = []; const el = document.getElementById('island');
      new MutationObserver(() => window.__islandSeen.push(el.dataset.state))
        .observe(el, { attributes: true, attributeFilter: ['data-state'] }); }""")
    wait_fn(page, "document.getElementById('island').dataset.state === 'completed'", timeout=120000)
    seen = page.evaluate("window.__islandSeen")
    check("capsule passes through analyzing states from real events",
          bool(set(seen) & {"queued", "analyzing", "findings"}), seen[:10])
    page.screenshot(path=f"{OUT}/01_capsule_completed.png")

    # 2. Overview to Findings: the capsule glides, the heading appears, rows stagger in.
    page.goto(BASE + "/#overview")
    page.wait_for_selector("section.view.active[data-view=overview]", timeout=8000)
    page.wait_for_timeout(900)
    page.screenshot(path=f"{OUT}/02_overview_settled.png")
    ind_before = page.evaluate("document.getElementById('capsuleInd').style.transform")
    page.click(".capsule-link[data-view=findings]")
    page.wait_for_timeout(130)
    page.screenshot(path=f"{OUT}/03_overview_to_findings_mid.png")
    page.wait_for_selector("section.view.active[data-view=findings] .finding", timeout=8000)
    page.wait_for_timeout(1100)
    ind_after = page.evaluate("document.getElementById('capsuleInd').style.transform")
    check("capsule indicator moves to Findings", ind_before != ind_after, f"{ind_before} -> {ind_after}")
    delays = page.evaluate("[...document.querySelectorAll('#findList .finding')].map(e => e.style.animationDelay)")
    check("finding rows carry staggered entry delays", len(set(delays)) > 1, delays[:4])
    page.screenshot(path=f"{OUT}/04_findings_settled.png")

    # 3. Open a finding: the row expands into the drawer. Pause mid-expansion for a frame.
    first_row_fid = page.evaluate("document.querySelector('#findList .finding').dataset.fid")
    page.focus("#findList .finding >> nth=0")
    click_and_freeze(page, "#findList .finding", 200)  # the row is focused first, as a user's click focuses it
    check("finding open creates an expansion ghost", ghosts(page) == 1, ghosts(page))
    shared = page.evaluate("""() => ({ clones: document.querySelectorAll('.fx-shared').length,
        rowTitle: getComputedStyle(document.querySelector('#findList .finding .finding-title')).visibility,
        drawerTitle: getComputedStyle(document.querySelector('#drawer .detail-title')).visibility,
        cloneText: [...document.querySelectorAll('.fx-shared')].map((e) => e.textContent.trim()).sort() })""")
    check("finding shared elements fly as two clones while the originals are held",
          shared["clones"] == 2 and shared["rowTitle"] == "hidden" and shared["drawerTitle"] == "hidden", shared)
    page.screenshot(path=f"{OUT}/05_finding_expand_mid.png")
    play_all(page)
    try:
        page.wait_for_selector("#drawer .detail[data-fid]", timeout=8000)
    except Exception:
        print("DEBUG drawer:", page.evaluate("""() => ({ cls: document.getElementById('drawer').className,
          body: document.getElementById('drawerBody').innerHTML.slice(0, 500),
          status: (window.NexusLive || {}).status })"""), file=sys.stderr)
        raise
    page.wait_for_timeout(900)
    page.screenshot(path=f"{OUT}/06_finding_detail.png")
    drawer_state = page.evaluate("""() => { const d = document.querySelector('#drawer .detail');
        return { has: !!d, fid: d && d.dataset.fid, open: document.getElementById('drawer').className,
          body: document.getElementById('drawerBody') && document.getElementById('drawerBody').innerHTML.slice(0, 300),
          pending: !!(window.__dbg) }; }""")
    check("finding drawer shows the same finding", drawer_state["fid"] == first_row_fid, drawer_state)

    # 4. Close with Escape: the panel contracts into its row and focus returns to that row.
    page.evaluate("""() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        document.getAnimations().forEach((a) => { a.pause(); a.currentTime = 120; }); }""")
    page.screenshot(path=f"{OUT}/07_finding_contract_mid.png")
    play_all(page)
    page.wait_for_timeout(700)
    check("Escape closes the drawer", page.evaluate("!document.getElementById('drawer').classList.contains('open')"))
    check("focus returns to the opening finding row",
          page.evaluate("document.activeElement && document.activeElement.dataset.fid") == first_row_fid,
          page.evaluate("document.activeElement && document.activeElement.className"))

    # 5. Live status change keeps the row element and position, and animates the tag.
    row_marker = page.evaluate("""() => { const r = document.querySelector('#findList .finding');
      r.__marker = 'kept'; return [...document.querySelectorAll('#findList .finding')].indexOf(r); }""")
    cookies = ctx.cookies(BASE)
    import urllib.request
    fid_detail = page.evaluate("""async () => { const a = (await (await fetch('/api/analyses', {credentials:'same-origin'})).json()).items.find(x => x.status === 'completed'); return a.id; }""")
    req = urllib.request.Request(
        f"{BASE}/api/analyses/{fid_detail}/findings/{first_row_fid}",
        data=json.dumps({"status": "confirmed"}).encode(), method="PATCH",
        headers={"Content-Type": "application/json", "Cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies)})
    status = urllib.request.urlopen(req, timeout=10).status
    check("finding status changed through the API", status == 200, status)
    wait_fn(page, "document.querySelector('#findList .finding .tag').textContent === 'Confirmed'", timeout=8000)
    kept = page.evaluate("""() => { const r = document.querySelector('#findList .finding');
      return { sameElement: r.__marker === 'kept', flashing: r.classList.contains('status-changed'),
               tagChanged: r.querySelector('.tag').classList.contains('changed'),
               position: [...document.querySelectorAll('#findList .finding')].indexOf(r) }; }""")
    check("status change keeps the row element and position", kept["sameElement"] and kept["position"] == row_marker,
          kept)
    check("status change animates the row and tag", kept["flashing"] and kept["tagChanged"], kept)
    page.wait_for_timeout(250)
    page.screenshot(path=f"{OUT}/08_status_change_mid.png")

    # 6. Filter change: the list re-renders with a stagger; the chip state is announced.
    page.click("[data-sev-filter=critical]")
    page.wait_for_timeout(120)
    page.screenshot(path=f"{OUT}/09_filter_critical_mid.png")
    page.wait_for_timeout(700)
    check("severity chip is pressed after filtering",
          page.get_attribute("[data-sev-filter=critical]", "aria-pressed") == "true")
    page.click("[data-sev-filter=all]")
    page.wait_for_timeout(500)

    # 7. Projects: card expands into details; closing contracts it back.
    page.goto(BASE + "/#projects")
    page.wait_for_selector("section.view.active[data-view=projects] .project", timeout=8000)
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/10_projects.png")
    page.focus("#projList [data-project]")
    click_and_freeze(page, "#projList [data-project]", 200)
    check("project open creates an expansion ghost", ghosts(page) == 1, ghosts(page))
    pshared = page.evaluate("""() => ({ clones: document.querySelectorAll('.fx-shared').length,
        marks: [...document.querySelectorAll('.project-mark')].map((e) => e.textContent) })""")
    check("project name and monogram fly as two clones", pshared["clones"] == 2, pshared)
    page.screenshot(path=f"{OUT}/11_project_expand_mid.png")
    play_all(page)
    page.wait_for_selector("#drawer #renameForm", timeout=8000)
    page.wait_for_timeout(900)
    page.screenshot(path=f"{OUT}/12_project_detail.png")
    page.click("#drawerClose")
    page.wait_for_timeout(700)
    check("close button closes the project drawer",
          page.evaluate("!document.getElementById('drawer').classList.contains('open')"))

    # 8. Browser back and forward keep their views and motion.
    page.go_back()
    page.wait_for_selector("section.view.active[data-view=findings]", timeout=5000)
    check("back returns to Findings", True)
    page.go_back()
    page.wait_for_selector("section.view.active[data-view=overview]", timeout=5000)
    check("back returns to Overview", True)
    page.go_forward()
    page.wait_for_selector("section.view.active[data-view=findings]", timeout=5000)
    check("forward returns to Findings", True)

    # 9. Profile menu (a dropdown) opens and shows its panel.
    page.goto(BASE + "/#overview")
    page.wait_for_selector("section.view.active[data-view=overview]", timeout=5000)
    page.click("#profileBtn")
    page.wait_for_timeout(350)
    check("profile menu opens", page.evaluate("document.getElementById('profileMenu').classList.contains('open')"))
    page.screenshot(path=f"{OUT}/13_profile_menu.png")
    page.keyboard.press("Escape")
    page.click("body", position={"x": 10, "y": 400})
    ctx.close()
    video_path = page.video.path() if page.video else None

    # ---------- Reduced motion: no ghosts, no staggered entries, focus still handled ----------
    rm = browser.new_context(viewport={"width": 1100, "height": 800}, reduced_motion="reduce")
    rpage = sign_in(rm, email, errors)
    wait_fn(rpage, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    rpage.goto(BASE + "/#findings")
    rpage.wait_for_selector("#findList .finding", timeout=8000)
    rpage.click("#findList .finding >> nth=0")
    created = ghosts(rpage)
    rpage.wait_for_selector("#drawer .detail[data-fid]", timeout=8000)
    check("reduced motion creates no expansion ghost", created == 0, created)
    rpage.keyboard.press("Escape")
    rpage.wait_for_timeout(200)
    check("reduced motion drawer still closes with Escape",
          rpage.evaluate("!document.getElementById('drawer').classList.contains('open')"))
    rpage.screenshot(path=f"{OUT}/14_reduced_motion_findings.png")
    rm.close()

    # ---------- Mobile: layouts hold at 390px through the new motion ----------
    mob = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mpage = sign_in(mob, email, errors)
    wait_fn(mpage, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    mpage.goto(BASE + "/#findings")
    mpage.wait_for_selector("#findList .finding", timeout=8000)
    mpage.wait_for_timeout(900)
    overflow = mpage.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check("mobile findings page does not overflow", overflow <= 0, overflow)
    mpage.screenshot(path=f"{OUT}/15_mobile_findings.png")
    # The admin.js finding has a 93-character evidence line, long enough to need sideways scrolling at 390px.
    mpage.locator("#findList .finding", has_text="admin.js").first.click()
    mpage.wait_for_selector("#drawer .detail[data-fid]", timeout=8000)
    mpage.wait_for_selector("#drawer .excerpt", timeout=8000)
    mpage.wait_for_timeout(900)
    drawer_overflow = mpage.evaluate("document.getElementById('drawer').scrollWidth - document.getElementById('drawer').clientWidth")
    check("mobile finding drawer does not overflow", drawer_overflow <= 0, drawer_overflow)
    ev = mpage.evaluate("(() => { const p = document.querySelector('.excerpt'); return { sw: p.scrollWidth, cw: p.clientWidth, scrollable: p.classList.contains('scrollable') }; })()")
    check("long evidence lines overflow the excerpt, which is marked scrollable", ev["sw"] > ev["cw"] and ev["scrollable"], ev)
    mpage.evaluate("document.querySelector('.excerpt').scrollLeft = 99999")
    end_scroll = mpage.evaluate("document.querySelector('.excerpt').scrollLeft")
    check("evidence scrolls sideways to the end of the longest line", end_scroll > 0, end_scroll)
    mpage.set_viewport_size({"width": 360, "height": 780})
    mpage.wait_for_timeout(400)
    narrow = mpage.evaluate("document.getElementById('drawer').scrollWidth - document.getElementById('drawer').clientWidth")
    check("360px drawer does not overflow", narrow <= 0, narrow)
    mpage.screenshot(path=f"{OUT}/17_mobile_drawer_360.png")
    mpage.screenshot(path=f"{OUT}/16_mobile_finding_drawer.png")
    mob.close()

    check("no page errors during the motion journey", not errors, errors[:3])
    browser.close()

    if video_path:
        check("video recorded", os.path.exists(video_path), video_path)

passed = sum(1 for r in results if r["ok"])
print(json.dumps({"passed": passed, "total": len(results), "checks": results, "outDir": OUT}, indent=2))
sys.exit(0 if passed == len(results) else 1)
