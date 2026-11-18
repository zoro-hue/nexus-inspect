"""Polling wait for Playwright pages under a strict Content Security Policy.

Playwright's string-based wait_for_function evaluates through eval, which the app's CSP (script-src 'self') blocks.
page.evaluate works under the same policy, so this helper polls a boolean expression with it instead.
"""
import time


def wait_fn(page, expression, timeout=5000):
    deadline = time.time() + timeout / 1000.0
    last = None
    while time.time() < deadline:
        try:
            if page.evaluate(f"!!({expression})"):
                return
        except Exception as exc:  # page may be navigating; retry until the deadline
            last = exc
        time.sleep(0.15)
    raise TimeoutError(f"condition not met within {timeout} ms: {expression}: {last}")
