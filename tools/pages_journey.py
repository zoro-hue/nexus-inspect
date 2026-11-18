"""Browser journey for documentation, API reference, navigation links, project rename and mobile layout.

Usage: python tools/pages_journey.py [BASE_URL] [FIXTURE_ZIP]
Runs in headless Chromium against a running server. Prints JSON and exits 1 on any failure.
"""
import json
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _wait import wait_fn
import os
import tempfile
import sys
import time
import urllib.request
import uuid

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ZIP = sys.argv[2] if len(sys.argv) > 2 else os.path.join(tempfile.gettempdir(), "nx", "demo-repository.zip")
if not os.path.exists(ZIP):
    import io, zipfile, tempfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("repo/app.js", b'console.log("hello");\n')
    tmp_zip = os.path.join(tempfile.gettempdir(), "demo-repository.zip")
    with open(tmp_zip, "wb") as fh:
        fh.write(buf.getvalue())
    ZIP = tmp_zip
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"  [{detail}]"), flush=True)


def catalog_size():
    with urllib.request.urlopen(BASE + "/api/reference", timeout=10) as r:
        return len(json.loads(r.read())["items"])


def main():
    expected_endpoints = catalog_size()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])

        # ----- Public pages, signed out -----
        ctx = browser.new_context(viewport={"width": 1366, "height": 860})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

        page.goto(BASE + "/#docs/introduction")
        page.wait_for_selector(".doc-title")
        check("docs introduction renders", page.inner_text(".doc-title") == "Introduction")
        check("docs table of contents lists all 18 sections", page.locator("#docsToc .doc-link").count() == 18,
              page.locator("#docsToc .doc-link").count())
        check("docs title uses Geist Pixel", "Geist Pixel" in page.evaluate(
            "getComputedStyle(document.querySelector('.doc-title')).fontFamily"))

        page.fill("#docsSearch", "exports")
        check("docs search narrows the table of contents", 0 < page.locator("#docsToc .doc-link").count() < 18,
              page.locator("#docsToc .doc-link").count())
        page.fill("#docsSearch", "")
        check("docs search clears", page.locator("#docsToc .doc-link").count() == 18)

        page.click(".doc-pager a.btn >> nth=-1")
        page.wait_for_selector(".doc-title:has-text('Quickstart')")
        check("pager moves to next section", page.evaluate("location.hash") == "#docs/quickstart")
        page.go_back()
        page.wait_for_selector(".doc-title:has-text('Introduction')")
        check("browser back returns to previous section", page.evaluate("location.hash") == "#docs/introduction")

        page.goto(BASE + "/#docs/rules")
        page.wait_for_selector(".doc-rule")
        api_count = page.evaluate("fetch('/api/rules').then((r) => r.json()).then((d) => d.items.length)")
        check("rule catalog renders every rule the API reports", page.locator(".doc-rule").count() == api_count,
              {"rendered": page.locator(".doc-rule").count(), "api": api_count})

        page.goto(BASE + "/#api")
        page.wait_for_selector(".api-ep")
        check(f"API reference documents all {expected_endpoints} catalogued endpoints",
              page.locator(".api-ep").count() == expected_endpoints, page.locator(".api-ep").count())
        page.fill("#docsSearch", "projects")
        check("API search filters endpoints", 0 < page.locator(".api-ep").count() < expected_endpoints,
              page.locator(".api-ep").count())
        page.fill("#docsSearch", "")
        page.locator("#docsToc .api-toc-link", has_text="/api/analyses/{analysisId}/report").first.click()
        page.wait_for_timeout(700)
        check("API table of contents link scrolls to its endpoint",
              page.evaluate("location.hash").startswith("#api/get-"), page.evaluate("location.hash"))
        page.locator(".api-ep .btn").first.click()
        page.wait_for_timeout(200)
        copy_label = page.inner_text(".api-ep .btn").strip()
        check("API copy button responds", copy_label in ("Copied", "Copy unavailable"), copy_label)

        # Navigation from the marketing header.
        page.goto(BASE + "/#home")
        page.wait_for_selector("[data-menu=docs]")
        page.hover("[data-menu=docs]")
        page.wait_for_selector("[data-panel=docs]:not([hidden]) a[href='#api']")
        page.click("[data-panel=docs]:not([hidden]) a[href='#api']")
        page.wait_for_selector(".api-ep")
        check("Documentation menu reaches API reference", page.evaluate("location.hash") == "#api")

        page.goto(BASE + "/#home")
        page.wait_for_selector(".hm-foot .hm-foot-col a")
        hrefs = page.eval_on_selector_all(".hm-foot .hm-foot-col a", "els => els.map(e => e.getAttribute('href'))")
        check("footer links are real destinations", hrefs and "#" not in hrefs and len(set(hrefs)) == len(hrefs), hrefs)
        for h in ["#docs/quickstart", "#api", "#docs/rules", "#signin"]:
            page.goto(BASE + "/" + h)
            page.wait_for_timeout(250)
        check("no in-page link points to a missing element",
              page.evaluate("""() => [...document.querySelectorAll('a[href^="#"]')].every(a => {
                  const t = a.getAttribute('href').slice(1);
                  return t === '' || ['home','signin','signup','analyze','overview','findings','projects','reports','docs','api'].includes(t.split('/')[0])
                    || !!document.getElementById(t) || !!document.querySelector('[data-view="' + t + '"]');
              })"""))

        # Mobile layout for the public pages.
        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        m = mobile.new_page()
        m.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        for route in ["#docs/introduction", "#api"]:
            m.goto(BASE + "/" + route)
            m.wait_for_selector(".doc-title")
            m.wait_for_timeout(400)
            overflow = m.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check(f"no horizontal overflow on {route} at 390px", overflow <= 1, overflow)
        mobile.close()

        # ----- Signed-in: project rename from the dashboard -----
        user = ctx.new_page()
        email = f"pages-{uuid.uuid4().hex[:8]}@example.com"
        user.goto(BASE + "/#signup")
        user.wait_for_selector("#signupForm:not([hidden])")
        user.fill("#signupName", "Pages Tester")
        user.fill("#signupEmail", email)
        user.fill("#signupPassword", "correct-horse-1")
        user.click("#signupForm button[type=submit]")
        user.wait_for_selector("#dash:not([hidden])", timeout=10000)
        with open(ZIP, "rb") as fh:
            payload = fh.read()
        resp = user.request.post(BASE + "/api/analyses?name=rename-me", data=payload,
                                 headers={"Content-Type": "application/zip"})
        check("upload creates an analysis for the signed-in user", resp.status in (201, 422), resp.status)
        user.goto(BASE + "/#projects")
        user.wait_for_selector(".project button", timeout=8000)
        user.locator(".project button").first.click()
        user.wait_for_selector("#renameForm")
        user.fill("#renameInput", "")
        user.click("#renameForm button[type=submit]")
        check("rename rejects an empty name", "between 1 and 120" in user.inner_text("#renameError"))
        new_name = f"Renamed {int(time.time())}"
        user.fill("#renameInput", new_name)
        user.click("#renameForm button[type=submit]")
        wait_fn(user, f"document.querySelector('#drawer .detail-title')?.textContent === {json.dumps(new_name)}", timeout=8000)
        check("rename saves and the project title updates", True)
        check("rename is reflected in the project list", new_name in user.inner_text("#projList"))

        # Signed-out visitors cannot rename: the API answers 401 for any project id.
        anon = browser.new_context()
        r = anon.request.patch(BASE + "/api/projects/prj_missing", data=json.dumps({"name": "x"}),
                               headers={"Content-Type": "application/json"})
        check("anonymous rename is refused", r.status == 401, r.status)
        anon.close()

        check("no page or console errors", not errors, "; ".join(errors[:3]))
        browser.close()

    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "checks": results}, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
