"""Live finding insertion through the real analysis pipeline (real Chromium, no seeded or fake findings).

Usage: python tools/live_insertion_journey.py [BASE_URL] [FIXTURE_ZIP]

Tab A sits on the Findings view of a brand-new account, which has zero findings. Tab B uploads a repository and starts
an analysis. Tab A never reloads. The test records:
  - how many rows were inserted into tab A while the analysis ran, and how many carried the entry animation class,
  - that the DOM rows match the persisted API findings (count, identity and severity tallies),
  - that the overview counter in tab A matches the API total,
  - that a dropped stream and reconnect leave row count and identities unchanged (no duplicates).
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
from collections import Counter

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ZIP = sys.argv[2] if len(sys.argv) > 2 else os.path.join(tempfile.gettempdir(), "nx", "demo-repository.zip")
OUT = os.environ.get("SHOTS", os.path.join(tempfile.gettempdir(), "nx", "insertion"))
os.makedirs(OUT, exist_ok=True)
SEVERITIES = ("critical", "high", "medium", "low", "info")

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})


def row_snapshot(page):
    return page.evaluate("""() => [...document.querySelectorAll('#findList .finding')].map((row) => {
        const sev = [...row.querySelector('.sev').classList].find((c) => ['critical','high','medium','low','info'].includes(c));
        return { fid: row.dataset.fid, severity: sev, leaving: row.classList.contains('leaving') }; })""")


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 860})
    errors = []

    a = ctx.new_page()
    a.on("pageerror", lambda e: errors.append(f"A pageerror: {e}"))
    a.on("console", lambda m: errors.append(f"A console: {m.text}") if m.type == "error" else None)

    email = f"insert-{os.getpid()}-{int(time.time())}@example.com"
    a.goto(BASE + "/#signup")
    a.wait_for_selector("#signupForm:not([hidden])", timeout=8000)
    a.fill("#signupName", "Insertion Tester")
    a.fill("#signupEmail", email)
    a.fill("#signupPassword", "correct-horse-1")
    a.click("#signupForm button[type=submit]")
    a.wait_for_selector("#dash:not([hidden])", timeout=10000)

    # Tab A: Findings view with zero findings, stream live.
    a.goto(BASE + "/#findings")
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=8000)
    wait_fn(a, "window.NexusLive && window.NexusLive.status === 'live'", timeout=8000)
    before = row_snapshot(a)
    check("tab A starts with zero finding rows", len(before) == 0, len(before))
    a.screenshot(path=f"{OUT}/01_findings_empty.png")

    # Record insertions in tab A without reloading: every row added, and whether it carried the entry class.
    a.evaluate("""() => { window.__inserted = []; const list = document.getElementById('findList');
      new MutationObserver((muts) => { muts.forEach((m) => m.addedNodes.forEach((n) => {
        if (n.nodeType === 1 && n.matches && n.matches('.finding')) {
          window.__inserted.push({ fid: n.dataset.fid, isNew: n.classList.contains('is-new'), anim: getComputedStyle(n).animationName, delay: n.style.animationDelay, t: performance.now() }); } })); })
        .observe(list, { childList: true, subtree: false }); }""")

    # Tab B starts the analysis through the real pipeline.
    b = ctx.new_page()
    b.on("pageerror", lambda e: errors.append(f"B pageerror: {e}"))
    b.goto(BASE + "/#analyze")
    b.wait_for_selector("#zipFile", state="attached")
    b.set_input_files("#zipFile", ZIP)
    wait_fn(b, "!document.getElementById('startBtn').disabled", timeout=5000)
    b.click("#startBtn")

    wait_fn(a, "document.querySelectorAll('#findList .finding').length > 0", timeout=120000)
    wait_fn(b, "document.getElementById('island').dataset.state === 'completed'", timeout=120000)
    a.wait_for_timeout(1500)

    inserted = a.evaluate("window.__inserted")
    dom = row_snapshot(a)
    check("rows are inserted into tab A without a reload", len(inserted) > 0, len(inserted))
    # A full rebuild enters rows with the staggered fadeUp; a single live insert uses is-new (finding-in).
    entering = [r for r in inserted if r["isNew"] or r["anim"] in ("fadeUp", "finding-in")]
    check("inserted rows enter with an entry animation (staggered fadeUp, or is-new)", len(entering) == len(inserted),
          {"entering": len(entering), "inserted": len(inserted), "sample": inserted[:1]})
    staggered = len({r["delay"] for r in inserted}) > 1
    check("the rows arrive staggered rather than all at once", staggered, sorted({r["delay"] for r in inserted})[:4])
    check("every DOM row was an insertion event (no unexplained rows)",
          {r["fid"] for r in inserted} >= {r["fid"] for r in dom}, len(dom))
    fids = [r["fid"] for r in dom]
    check("no duplicate finding rows in tab A", len(fids) == len(set(fids)), f"{len(fids)} rows, {len(set(fids))} unique")

    # Compare against the persisted API result (the source of truth).
    api = a.evaluate("""async () => {
      const list = await (await fetch('/api/analyses', {credentials:'same-origin'})).json();
      const done = list.items.find((x) => x.status === 'completed');
      const f = await (await fetch('/api/analyses/' + done.id + '/findings', {credentials:'same-origin'})).json();
      return { analysisId: done.id, items: f.items, total: f.summary.total }; }""")
    api_items = api["items"]
    api_fids = {f["id"] for f in api_items}
    check("DOM row identities match the persisted findings", set(fids) == api_fids,
          f"dom={len(fids)} api={len(api_fids)} missing={len(api_fids - set(fids))} extra={len(set(fids) - api_fids)}")
    dom_tally = Counter(r["severity"] for r in dom)
    api_tally = Counter(f["severity"] for f in api_items)
    check("DOM severity tallies match the persisted findings", dom_tally == api_tally,
          {"dom": dict(dom_tally), "api": dict(api_tally)})
    check("API summary total matches the number of persisted findings", api["total"] == len(api_items),
          f"summary={api['total']} items={len(api_items)}")
    a.goto(BASE + "/#overview")
    a.wait_for_selector("section.view.active[data-view=overview]", timeout=8000)
    wait_fn(a, f"document.getElementById('ovFindings').textContent === '{api['total']}'", timeout=15000)
    check("overview findings counter in tab A matches the API total", True, api["total"])
    a.goto(BASE + "/#findings")
    a.wait_for_selector("section.view.active[data-view=findings]", timeout=8000)
    a.wait_for_timeout(900)
    a.screenshot(path=f"{OUT}/02_findings_after_insertion.png")

    # Dropped stream, change made through the API meanwhile, reconnect: count and identities must not change.
    ctx.set_offline(True)
    wait_fn(a, "window.NexusLive.status !== 'live'", timeout=10000)
    ctx.set_offline(False)
    wait_fn(a, "window.NexusLive.status === 'live'", timeout=30000)
    a.wait_for_timeout(1500)
    after_reconnect = row_snapshot(a)
    fids2 = [r["fid"] for r in after_reconnect]
    check("reconnect leaves the row count unchanged", len(fids2) == len(fids), f"{len(fids)} -> {len(fids2)}")
    check("reconnect leaves no duplicate rows", len(fids2) == len(set(fids2)), f"{len(fids2)} rows, {len(set(fids2))} unique")
    check("reconnect keeps the same row identities", set(fids2) == set(fids))
    check("no rows left in a leaving state after reconnect", not any(r["leaving"] for r in after_reconnect))
    check("no page errors during insertion", not errors, errors[:3])

    ctx.close()
    browser.close()

report = {"checks": results, "passed": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}
print(json.dumps(report, indent=2))
sys.exit(1 if report["failed"] else 0)
