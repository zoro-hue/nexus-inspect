import os
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000"
SHOTS_DIR = "screenshots"
os.makedirs(SHOTS_DIR, exist_ok=True)

def capture_sequence(viewport, name_prefix):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        ctx = browser.new_context(viewport=viewport, reduced_motion="no-preference")
        page = ctx.new_page()

        page.goto(f"{BASE_URL}/#home")
        page.wait_for_timeout(300)

        # Scroll target into view
        # We target #statement
        page.evaluate("""() => {
            const el = document.querySelector('#statement');
            el.scrollIntoView({ block: 'center' });
        }""")

        timestamps = [0, 200, 400, 700, 1000]
        start_time = time.time()
        for ts in timestamps:
            target_time = start_time + (ts / 1000.0)
            sleep_duration = target_time - time.time()
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            shot_path = os.path.join(SHOTS_DIR, f"transition-{name_prefix}-{ts}ms.png")
            page.screenshot(path=shot_path)
            print(f"Captured {shot_path} at actual {(time.time() - start_time)*1000:.1f}ms")

        browser.close()

if __name__ == "__main__":
    print("Capturing 1440px sequence...")
    capture_sequence({"width": 1440, "height": 900}, "1440")
    print("Capturing 390px sequence...")
    capture_sequence({"width": 390, "height": 844}, "390")
    print("All sequences captured successfully.")
