"""Browser journey for authentication: fresh context, protected routes, sign-up, sign-out, sign-in, expiry.

Usage: python tools/auth_journey.py [BASE_URL]
Requires Playwright for Python with Chromium installed. Prints a JSON report and exits 1 on any failure.
"""
import json
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _wait import wait_fn
import sys
import time
import uuid

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASSWORD = "correct-horse-1"
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def main():
    email = f"journey-{uuid.uuid4().hex[:8]}@example.com"
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 860})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)

        # 1. Protected route from a fresh session redirects to sign-in with a reason.
        page.goto(f"{BASE}/#findings")
        page.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
        check("protected route redirects to sign-in", page.evaluate("location.hash") == "#signin")
        check("redirect explains why", "Sign in to open the dashboard" in page.inner_text("#authAlert"))
        check("dashboard hidden while signed out", page.is_hidden("#dash"))

        # 2. Client validation blocks an invalid sign-in without a network request.
        page.click("#signinForm button[type=submit]")
        check("empty sign-in shows field errors", "Enter your email" in page.inner_text("#signinForm [data-field=email] .field-error"))

        # 3. Sign-up switch and validation.
        page.click("#authSwitch a")
        page.wait_for_selector("#signupForm:not([hidden])")
        check("switch to sign-up", page.inner_text("#authTitle") == "Create your account")
        page.fill("#signupName", "Journey Tester")
        page.fill("#signupEmail", "not-an-email")
        page.fill("#signupPassword", "short")
        page.click("#signupForm button[type=submit]")
        check("sign-up rejects bad email", "valid email" in page.inner_text("#signupForm [data-field=email] .field-error"))
        check("sign-up rejects short password", "at least 10" in page.inner_text("#signupForm [data-field=password] .field-error").lower())

        # 4. Password visibility toggle.
        page.fill("#signupPassword", PASSWORD)
        page.click("#signupForm .pw-toggle")
        check("password toggle reveals value", page.get_attribute("#signupPassword", "type") == "text")
        check("toggle reports pressed state", page.get_attribute("#signupForm .pw-toggle", "aria-pressed") == "true")
        page.click("#signupForm .pw-toggle")

        # 5. Successful sign-up lands on the dashboard overview.
        page.fill("#signupEmail", email)
        page.click("#signupForm button[type=submit]")
        page.wait_for_selector("#dash:not([hidden])", timeout=10000)
        check("sign-up returns to the requested route", page.evaluate("location.hash") == "#findings", page.evaluate("location.hash"))
        check("profile button visible when signed in", page.is_visible("#profileBtn"))

        # 6. Profile menu opens, shows the account, and closes with Escape.
        page.click("#profileBtn")
        page.wait_for_selector("#profileMenu:not([hidden])")
        check("profile menu shows email", email in page.inner_text("#profileEmail"))
        page.keyboard.press("Escape")
        wait_fn(page, "document.getElementById('profileMenu').hidden === true", timeout=3000)
        check("Escape closes profile menu", True)

        # 7. Session persists across reload.
        page.reload()
        page.wait_for_selector("#dash:not([hidden])", timeout=10000)
        check("session persists after reload", page.is_visible("#dash"))

        # 8. Sign out clears the session and blocks the dashboard again.
        page.click("#profileBtn")
        page.click("#signOutBtn")
        page.wait_for_selector("#signinForm:not([hidden])", timeout=10000)
        check("sign-out returns to sign-in", page.evaluate("location.hash") == "#signin")
        check("sign-out message shown", "signed out" in page.inner_text("#authAlert").lower())
        page.goto(f"{BASE}/#projects")
        page.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
        check("protected route blocked after sign-out", page.is_hidden("#dash"))

        # 9. Sign-in with wrong password shows a generic error.
        page.fill("#signinEmail", email)
        page.fill("#signinPassword", "wrong-horse-1")
        page.click("#signinForm button[type=submit]")
        wait_fn(page, "document.getElementById('authAlert').hidden === false", timeout=8000)
        check("wrong password shows generic error", "incorrect" in page.inner_text("#authAlert").lower())

        # 10. Correct sign-in returns the user to the protected route they asked for.
        page.fill("#signinPassword", PASSWORD)
        page.click("#signinForm button[type=submit]")
        page.wait_for_selector("#dash:not([hidden])", timeout=10000)
        check("sign-in returns to dashboard", page.evaluate("location.hash") in ("#overview", "#projects"))

        # 11. Expired session in the middle of a session sends the user to sign-in.
        ctx.clear_cookies()
        page.evaluate("window.dispatchEvent(new Event('nexus:unauthorized'))")
        page.wait_for_selector("#signinForm:not([hidden])", timeout=8000)
        check("unauthorized event returns to sign-in", "Your session has ended" in page.inner_text("#authAlert"))

        check("no uncaught page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "results": results}, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
